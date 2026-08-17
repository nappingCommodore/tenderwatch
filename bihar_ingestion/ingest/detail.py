"""Detail ingestion: fetch tender detail, purchase order, and corrigendum
payloads for tenders sitting in the discovery queue."""

from __future__ import annotations

from ..db import Database
from ..http_client import PortalClient, PortalError

_PREVIEW = "/rest/quotation/previewTenderByTenderId"
_PO = "/rest/openarea/getPoDetailsForPastTender"
_CORR = "/rest/corrigendum/getPublishedCorrigendumByTenderId"

# Tabs whose tenders can have an awarded purchase order.
_PO_STATUSES = {"CLOSED"}


class DetailWorker:
    """Fetches per-tender detail payloads and archives them."""

    def __init__(self, client: PortalClient, db: Database, force: bool = False) -> None:
        self.client = client
        self.db = db
        self.force = force

    # -- queue selection ---------------------------------------------------
    def _pending(self, flag: str, extra_where: str = "") -> list:
        cond = "" if self.force else f"WHERE {flag} = 0"
        if extra_where:
            cond = f"{cond} AND {extra_where}" if cond else f"WHERE {extra_where}"
        return self.db.query(
            f"SELECT tender_id, listing_status, source_tab, corrigendum_flag "
            f"FROM tender_discovery {cond} ORDER BY tender_id"
        )

    # -- runners -----------------------------------------------------------
    def fetch_details(self, limit: int | None = None) -> int:
        rows = self._pending("detail_fetched")
        if limit:
            rows = rows[:limit]
        total = len(rows)
        print(f"[details] fetching tender detail for {total} tenders...", flush=True)
        count = 0
        for i, row in enumerate(rows, 1):
            tid = row["tender_id"]
            if self._fetch_one(_PREVIEW, "detail:preview", "tender", tid):
                self.db.execute(
                    "UPDATE tender_discovery SET detail_fetched = 1 WHERE tender_id = ?",
                    (tid,),
                )
                count += 1
            self.db.commit()
            self._progress("details", i, total)
        return count

    def fetch_purchase_orders(self, limit: int | None = None) -> int:
        rows = self._pending("po_fetched")
        rows = [r for r in rows if (r["listing_status"] in _PO_STATUSES
                                    or r["source_tab"] == "past")]
        if limit:
            rows = rows[:limit]
        total = len(rows)
        print(f"[purchase_orders] fetching PO for {total} closed tenders...", flush=True)
        count = 0
        for i, row in enumerate(rows, 1):
            tid = row["tender_id"]
            # PO may legitimately be empty; still mark as attempted.
            fetched = self._fetch_one(_PO, "detail:po", "po", tid, allow_empty=True)
            self.db.execute(
                "UPDATE tender_discovery SET po_fetched = 1 WHERE tender_id = ?", (tid,)
            )
            count += int(fetched)
            self.db.commit()
            self._progress("purchase_orders", i, total)
        return count

    def fetch_corrigenda(self, limit: int | None = None) -> int:
        rows = self._pending("corr_fetched")
        rows = [r for r in rows if (r["source_tab"] == "corrigendum"
                                    or (r["corrigendum_flag"] or "").upper() == "Y")]
        if limit:
            rows = rows[:limit]
        total = len(rows)
        print(f"[corrigenda] fetching corrigenda for {total} tenders...", flush=True)
        count = 0
        for i, row in enumerate(rows, 1):
            tid = row["tender_id"]
            fetched = self._fetch_one(_CORR, "detail:corrigendum", "corrigendum", tid,
                                      allow_empty=True)
            self.db.execute(
                "UPDATE tender_discovery SET corr_fetched = 1 WHERE tender_id = ?", (tid,)
            )
            count += int(fetched)
            self.db.commit()
            self._progress("corrigenda", i, total)
        return count

    def _progress(self, phase: str, done: int, total: int, every: int = 25) -> None:
        if done % every == 0 or done == total:
            pct = (done / total * 100) if total else 100
            print(f"  [{phase}] {done}/{total} ({pct:.1f}%)", flush=True)

    # -- shared fetch ------------------------------------------------------
    def _fetch_one(
        self, path: str, endpoint: str, entity_type: str, tender_id: int,
        allow_empty: bool = False,
    ) -> bool:
        try:
            payload = self.client.post_json(endpoint, path, {"tenderId": tender_id})
        except PortalError as exc:
            self.db.log_error(entity_type, str(exc), endpoint=endpoint,
                              entity_key=str(tender_id))
            return False
        if payload is None or (payload == [] and not allow_empty):
            return False
        if payload == [] or payload == {}:
            if not allow_empty:
                return False
        self.db.archive_raw(endpoint, entity_type, str(tender_id), payload, http_status=200)
        return bool(payload) or allow_empty
