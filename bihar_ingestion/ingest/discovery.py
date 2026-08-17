"""Discovery ingestion: paginate the listing tabs into the tender work queue."""

from __future__ import annotations

from dataclasses import dataclass

from ..db import Database
from ..http_client import PortalClient, PortalError
from ..utils import clean_text, coerce_int, now_iso, sane_epoch


@dataclass(frozen=True)
class TabSpec:
    tab: str
    endpoint: str
    path: str
    listing_status: str
    paginated: bool


# Maps configured tab names to their listing endpoints. `paginated` marks the
# tabs that accepted startpoint/maxRow in the captured HAR; the others are
# probed with pagination but usually return a single page.
TAB_SPECS: dict[str, TabSpec] = {
    "open": TabSpec("open", "discovery:open",
                    "/rest/openarea/getTenderList", "OPEN", True),
    "past": TabSpec("past", "discovery:past",
                    "/rest/openarea/getPastTenders", "CLOSED", True),
    "cancelled": TabSpec("cancelled", "discovery:cancelled",
                         "/rest/openarea/getCancelTenderList", "CANCELLED", True),
    "upcoming": TabSpec("upcoming", "discovery:upcoming",
                        "/rest/openarea/getUpcommingTenders", "UPCOMING", False),
    "corrigendum": TabSpec("corrigendum", "discovery:corrigendum",
                           "/rest/openarea/getCorrigendumTenders", "CORRIGENDUM", False),
}


class DiscoveryCrawler:
    """Crawls listing tabs and upserts discovered tenders into the queue."""

    def __init__(self, client: PortalClient, db: Database) -> None:
        self.client = client
        self.db = db

    def run(self, tabs: tuple[str, ...]) -> dict[str, int]:
        results: dict[str, int] = {}
        # The listing is scoped by a single orgId. The portal root (538,
        # "Government of Bihar") returns every tender, so one pass covers the
        # whole portal — there is no need to enumerate sub-organizations.
        org_id = self.client.settings.portal.discovery_org_id
        for tab in tabs:
            spec = TAB_SPECS.get(tab)
            if spec is None:
                continue
            results[tab] = self._crawl_tab(spec, org_id)
        return results

    @staticmethod
    def _filter_body(org_id: int | None) -> dict:
        # Mirrors the portal's listing filter payload. Empty strings mean "no
        # filter" for that facet; orgId scopes the crawl to an organization.
        return {
            "orgId": str(org_id) if org_id is not None else "",
            "deptId": "",
            "dateParam": "",
            "startDate": None,
            "endDate": None,
            "closeDateFrom": "",
            "closeDateTo": "",
            "procatId": "",
            "typeId": "",
            "textFilter": None,
        }

    def _crawl_tab(self, spec: TabSpec, org_id: int | None) -> int:
        page_size = self.client.settings.pagination.page_size
        max_pages = self.client.settings.pagination.max_pages
        force = self.client.settings.crawl.force_refetch
        body = self._filter_body(org_id)
        state_key = f"{spec.endpoint}:{org_id}"

        # Resume support: pick up from the saved cursor unless the tab already
        # completed (exhausted) or a full re-scan is forced.
        prior = self.db.query(
            "SELECT next_startpoint, exhausted FROM crawl_state WHERE endpoint = ?",
            (state_key,),
        )
        resume_start = 0
        if prior and not force:
            if prior[0]["exhausted"]:
                return 0  # already fully crawled; nothing to do
            resume_start = prior[0]["next_startpoint"] or 0

        seen_ids: set[int] = set()
        reached_end = False
        try:
            if spec.paginated:
                for startpoint, rows in self.client.paginate(
                    spec.endpoint,
                    spec.path,
                    method="POST",
                    page_size=page_size,
                    max_pages=max_pages,
                    start=resume_start,
                    json_body=body,
                ):
                    page_ids = self._ingest_page(spec, org_id, startpoint, rows)
                    fresh = page_ids - seen_ids
                    seen_ids |= page_ids
                    self._update_state(state_key, startpoint + page_size,
                                       page_size, len(seen_ids))
                    self.db.commit()
                    # Guard against a server that clamps the offset and replays
                    # the last page instead of returning empty: if a full page
                    # contributed no new tender ids, we've reached the end.
                    if page_ids and not fresh:
                        reached_end = True
                        break
                else:
                    # paginate() returned normally => it hit an empty page.
                    reached_end = True
            else:
                # These endpoints were observed without pagination params.
                rows = self.client.post_json(spec.endpoint, spec.path, json_body=body)
                if not isinstance(rows, list):
                    rows = [] if rows is None else [rows]
                seen_ids |= self._ingest_page(spec, org_id, 0, rows)
                self._update_state(state_key, 0, len(rows), len(seen_ids))
                self.db.commit()
                reached_end = True
        except PortalError as exc:
            # Stopped mid-crawl (e.g. transient outage): leave the cursor in
            # place and NOT exhausted so a re-run resumes from where we stopped.
            self.db.log_error("discovery", str(exc), endpoint=state_key)
        if reached_end:
            self._mark_exhausted(state_key)
        self.db.commit()
        return len(seen_ids)

    def _ingest_page(self, spec: TabSpec, org_id: int | None, startpoint: int,
                     rows: list) -> set[int]:
        self.db.archive_raw(
            spec.endpoint, "listing", f"{spec.tab}:{org_id}:{startpoint}", rows,
            http_status=200,
        )
        ids: set[int] = set()
        for row in rows:
            tid = self._upsert_row(spec, row)
            if tid is not None:
                ids.add(tid)
        return ids

    def _upsert_row(self, spec: TabSpec, row: dict) -> int | None:
        if not isinstance(row, dict):
            return None
        tender_id = coerce_int(row.get("currenttenderid")) or coerce_int(row.get("tenderid"))
        if not tender_id:
            return None

        now = now_iso()
        existing = self.db.scalar(
            "SELECT first_seen_at FROM tender_discovery WHERE tender_id = ?", (tender_id,)
        )
        record = {
            "tender_id": tender_id,
            "org_tender_id": coerce_int(row.get("currentOrgTenderId")),
            "ref_no": clean_text(row.get("currenttenderrefno")),
            "status_code": coerce_int(row.get("currentstatus")),
            "org_id": coerce_int(row.get("currentorgid")),
            "dept_id": coerce_int(row.get("currentdeptid")),
            "proc_cat_id": coerce_int(row.get("currentproccatid")),
            "tender_type_id": coerce_int(row.get("currenttendertypeid")),
            "tender_cat_id": coerce_int(row.get("currenttendercatid")),
            "description": clean_text(row.get("currentdescription")),
            "publish_epoch": sane_epoch(row.get("currentTenderPublishDate")),
            "close_epoch": sane_epoch(row.get("currentbidEndDate")),
            "corrigendum_flag": clean_text(row.get("corrigendumFlag")),
            "first_seen_at": existing or now,
            "last_seen_at": now,
        }
        if existing is None:
            # First tab to surface a tender wins its provenance labels and
            # initializes the work-tracking flags. A tender can appear in several
            # tabs (e.g. a past tender that also has a corrigendum); its true
            # state comes from the detail payload (status_code), not the tab.
            record["source_tab"] = spec.tab
            record["listing_status"] = spec.listing_status
            record["detail_fetched"] = 0
            record["po_fetched"] = 0
            record["corr_fetched"] = 0
        self.db.upsert("tender_discovery", record, pk="tender_id")
        return tender_id

    def _update_state(self, state_key: str, next_start: int, page_size: int, seen: int) -> None:
        self.db.upsert(
            "crawl_state",
            {
                "endpoint": state_key,
                "next_startpoint": next_start,
                "page_size": page_size,
                "exhausted": 0,
                "last_run_at": now_iso(),
                "rows_seen": seen,
            },
            pk="endpoint",
        )

    def _mark_exhausted(self, state_key: str) -> None:
        self.db.execute(
            "UPDATE crawl_state SET exhausted = 1, last_run_at = ? WHERE endpoint = ?",
            (now_iso(), state_key),
        )
