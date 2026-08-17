"""Master-data ingestion: fetch dimension source payloads and archive them."""

from __future__ import annotations

import json
from collections import deque

from ..db import Database
from ..http_client import PortalClient, PortalError
from ..utils import coerce_int

_LOAD_DEPT = "/rest/organization/loadAllDeptWithParent"


class MasterLoader:
    """Fetches the portal's master/reference data and archives raw payloads."""

    def __init__(self, client: PortalClient, db: Database) -> None:
        self.client = client
        self.db = db

    def run(self) -> dict[str, int]:
        """Fetch all master payloads. Returns a count of archived responses."""

        counts: dict[str, int] = {}

        counts["date_time_format"] = self._fetch(
            "masters:getDefaultDateTimeFormat",
            "POST",
            "/rest/master/getDefaultDateTimeFormat",
            entity_type="master",
            entity_key="dateTimeFormat",
        )
        counts["openarea_master"] = self._fetch(
            "masters:getMasterListForOpenareaTenderListing",
            "POST",
            "/rest/master/getMasterListForOpenareaTenderListing",
            entity_type="master",
            entity_key="openareaMasterList",
        )
        counts["rfq_proccat_bidpart"] = self._fetch(
            "masters:showRfqCategoryRfqTypeProcCatBidPartList",
            "POST",
            "/rest/master/showRfqCategoryRfqTypeProcCatBidPartList",
            entity_type="master",
            entity_key="rfqProcCatBidPart",
        )

        dept_count = 0
        for org_id in self.client.settings.portal.root_org_ids:
            got = self._fetch(
                "masters:loadAllDeptWithParent",
                "POST",
                "/rest/organization/loadAllDeptWithParent",
                entity_type="department",
                entity_key=f"org:{org_id}",
                params={"orgId": org_id},
            )
            dept_count += got
        counts["departments"] = dept_count

        self.db.commit()
        return counts

    def expand_department_tree(self, max_calls: int = 4000) -> dict[str, int]:
        """Walk the organization hierarchy so dim_department covers deep leaf
        offices (the ones tenders are actually issued by).

        ``loadAllDeptWithParent(orgId=X)`` returns the descendants of X but not
        all of them recursively from the top (the root call is capped). A BFS
        that calls each discovered org names the whole tree. Already-fetched
        orgs are skipped, so this is resumable and cheap to re-run.
        """

        # Seed the "already fetched" set from prior runs (resume-friendly).
        # Use the call log so even orgs whose response was empty (leaves, hence
        # not archived) are skipped — avoids re-hitting them on a resumed pass.
        visited: set[int] = set()
        for r in self.db.query(
            "SELECT params FROM api_call_log "
            "WHERE endpoint = 'masters:loadAllDeptWithParent' AND params IS NOT NULL"
        ):
            try:
                oid = coerce_int(json.loads(r["params"]).get("orgId"))
            except (ValueError, TypeError, AttributeError):
                oid = None
            if oid is not None:
                visited.add(oid)
        for r in self.db.query(
            "SELECT entity_key FROM raw_response WHERE entity_type = 'department'"
        ):
            key = (r["entity_key"] or "")
            if key.startswith("org:"):
                oid = coerce_int(key[4:])
                if oid is not None:
                    visited.add(oid)

        # Seed the queue with the roots, every org we already know, and every id
        # that appears in a tender's department path (so referenced leaves get
        # named first even under a low call budget).
        queue: deque[int] = deque()
        seeded: set[int] = set()

        def enqueue(oid: int | None) -> None:
            if oid is not None and oid not in seeded:
                seeded.add(oid)
                queue.append(oid)

        for rid in self.client.settings.portal.root_org_ids:
            enqueue(rid)
        for r in self.db.query("SELECT department_id FROM dim_department"):
            enqueue(r["department_id"])
        for r in self.db.query(
            "SELECT DISTINCT dept_path FROM fact_tender WHERE dept_path IS NOT NULL"
        ):
            for tok in str(r["dept_path"]).split("."):
                enqueue(coerce_int(tok))

        calls = 0
        archived = 0
        print(f"[expand-departments] starting; {len(queue)} orgs queued, "
              f"{len(visited)} already fetched", flush=True)
        while queue and calls < max_calls:
            oid = queue.popleft()
            if oid in visited:
                continue
            visited.add(oid)
            try:
                resp = self.client.post_json(
                    "masters:loadAllDeptWithParent", _LOAD_DEPT, {"orgId": oid}
                )
                calls += 1
            except PortalError as exc:
                self.db.log_error("masters", str(exc), endpoint="expandDept",
                                  entity_key=f"org:{oid}")
                self.db.commit()
                continue
            if isinstance(resp, list) and resp:
                self.db.archive_raw("masters:loadAllDeptWithParent", "department",
                                    f"org:{oid}", resp, http_status=200)
                archived += 1
                for org in resp:
                    if isinstance(org, dict):
                        enqueue(coerce_int(org.get("organizationId")))
            # Commit periodically (not only on non-empty responses) so progress
            # is durable + visible even through long runs of empty leaf orgs.
            if calls % 20 == 0:
                self.db.commit()
                print(f"  [expand-departments] {calls} calls, {archived} subtrees, "
                      f"{len(queue)} queued", flush=True)
        self.db.commit()
        print(f"[expand-departments] done: {calls} calls, {archived} subtrees, "
              f"{len(visited)} orgs visited", flush=True)
        return {"calls": calls, "archived": archived, "orgs_visited": len(visited)}

    def _fetch(
        self,
        endpoint: str,
        method: str,
        path: str,
        entity_type: str,
        entity_key: str,
        params: dict | None = None,
    ) -> int:
        try:
            payload = (
                self.client.post_json(endpoint, path, params)
                if method == "POST"
                else self.client.get_json(endpoint, path, params)
            )
        except PortalError as exc:
            self.db.log_error("masters", str(exc), endpoint=endpoint, entity_key=entity_key)
            return 0
        if payload is None:
            return 0
        self.db.archive_raw(endpoint, entity_type, entity_key, payload, http_status=200)
        return 1
