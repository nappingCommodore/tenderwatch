"""Parse archived tender-detail payloads into fact_tender and fact_document."""

from __future__ import annotations

import json
from typing import Any

from ..db import Database
from ..utils import (
    clean_text,
    coerce_float,
    coerce_int,
    epoch_ms_to_iso,
    now_iso,
    parse_dept_path,
    sane_epoch,
)
from ._base import BaseParser
from .documents import extract_documents
from .fields import collect_field_values


class TenderParser(BaseParser):
    """Populates fact_tender (+ documents) from tender detail payloads."""

    def __init__(self, db: Database, base_url: str) -> None:
        super().__init__(db)
        self.base_url = base_url

    def run(self) -> dict[str, int]:
        now = now_iso()
        parents: dict[int, int] = {}
        tender_count = 0
        doc_count = 0

        # Discovery context (source_tab / listing_status) keyed by tender id.
        listing = {
            r["tender_id"]: {
                "source_tab": r["source_tab"],
                "listing_status": r["listing_status"],
            }
            for r in self.db.query(
                "SELECT tender_id, source_tab, listing_status FROM tender_discovery"
            )
        }

        for raw in self.db.iter_latest_raw("tender"):
            payload = json.loads(raw["payload"])
            if not isinstance(payload, dict):
                continue
            tid = coerce_int(payload.get("tenderid"))
            if tid is None:
                continue

            ctx = listing.get(tid, {})
            parent_id = coerce_int(payload.get("parentid"))
            if parent_id:
                parents[tid] = parent_id

            self._ensure_dims(payload)

            row = self._map_tender(payload, ctx, raw["id"])
            self.upsert_with_seen("fact_tender", row, pk="tender_id", now=now)
            tender_count += 1

            doc_count += self._parse_documents(payload, tid, raw["id"], now)
            self.db.commit()

        self._link_parents(parents)
        self.db.commit()
        return {"tenders": tender_count, "documents": doc_count}

    # -- mapping -----------------------------------------------------------
    def _ensure_dims(self, payload: dict) -> None:
        self.ensure_department(coerce_int(payload.get("deptid")))
        self.ensure_proc_cat(coerce_int(payload.get("proccatid")))
        self.ensure_rfq_type(coerce_int(payload.get("tendertypeid")))
        self.ensure_currency(clean_text(payload.get("tendercurrency")))
        self.ensure_currency(clean_text(payload.get("bidcurrency")))
        self.ensure_status("tender", coerce_int(payload.get("status")))

    # Bid/timeline dates live inside templateMap fields, keyed by shortName.
    _DATE_FIELDS = ("bid_start_date", "bid_end_date", "bid_open_date", "doc_sub_date")

    def _map_tender(self, p: dict, ctx: dict, raw_id: int) -> dict[str, Any]:
        dept_path = parse_dept_path(p.get("queryString"))
        dates = collect_field_values(p, self._DATE_FIELDS)
        return {
            "tender_id": coerce_int(p.get("tenderid")),
            "org_tender_id": coerce_int(p.get("orgtenderid")),
            "ref_no": clean_text(p.get("tenderrefno")),
            "group_id": coerce_int(p.get("groupid")),
            "parent_tender_id": None,  # linked in a second pass
            "org_id": coerce_int(p.get("orgid")),
            "dept_id": coerce_int(p.get("deptid")),
            "tender_type_id": coerce_int(p.get("tendertypeid")),
            "tender_cat_id": coerce_int(p.get("tendercatid")),
            "proc_cat_id": coerce_int(p.get("proccatid")),
            "bid_part_no": coerce_int(p.get("bidPartNo")),
            "status_code": coerce_int(p.get("status")),
            "listing_status": clean_text(ctx.get("listing_status")),
            "source_tab": clean_text(ctx.get("source_tab")),
            "description": clean_text(p.get("description")),
            "nit": clean_text(p.get("nit")),
            "ranking_sequence": clean_text(p.get("rankingsequence")),
            "pac_amount": coerce_float(p.get("pacamt")),
            "tender_currency": clean_text(p.get("tendercurrency")),
            "bid_currency": clean_text(p.get("bidcurrency")),
            "min_bid_no": coerce_int(p.get("minbidno")),
            "tender_call_no": coerce_int(p.get("tendercallno")),
            "offer_validity_days": coerce_int(p.get("offerValidity")),
            "pki_enabled": clean_text(p.get("pkiEnabled")),
            "auction_flag": clean_text(p.get("auctionflag")),
            "dept_path": ".".join(str(d) for d in dept_path) if dept_path else None,
            "issuing_authority_id": coerce_int(p.get("tenderIssuingAuthorityId")),
            "approving_authority_id": coerce_int(p.get("tenderApprovingAuthorityId")),
            "publish_epoch": sane_epoch(p.get("publishdate")),
            "publish_date": epoch_ms_to_iso(p.get("publishdate")),
            "bid_start_epoch": sane_epoch(dates.get("bid_start_date")),
            "bid_start_date": epoch_ms_to_iso(dates.get("bid_start_date")),
            "bid_end_epoch": sane_epoch(dates.get("bid_end_date")),
            "bid_end_date": epoch_ms_to_iso(dates.get("bid_end_date")),
            "bid_open_epoch": sane_epoch(dates.get("bid_open_date")),
            "bid_open_date": epoch_ms_to_iso(dates.get("bid_open_date")),
            "doc_submission_end_epoch": sane_epoch(dates.get("doc_sub_date")),
            "doc_submission_end_date": epoch_ms_to_iso(dates.get("doc_sub_date")),
            "cancel_epoch": sane_epoch(p.get("tenderCancelDate")),
            "cancel_date": epoch_ms_to_iso(p.get("tenderCancelDate")),
            "cancel_reason": clean_text(p.get("tenderCancelReason")),
            "create_epoch": sane_epoch(p.get("createdate")),
            "update_epoch": sane_epoch(p.get("updatedate")),
            "raw_response_id": raw_id,
        }

    def _parse_documents(self, payload: dict, tid: int, raw_id: int, now: str) -> int:
        docs = extract_documents(payload, self.base_url)
        for doc in docs:
            self.upsert_document(doc, tender_id=tid, source="tender",
                                 raw_id=raw_id, now=now)
        return len(docs)

    def _link_parents(self, parents: dict[int, int]) -> None:
        for child, parent in parents.items():
            exists = self.db.scalar(
                "SELECT 1 FROM fact_tender WHERE tender_id = ?", (parent,)
            )
            if exists:
                self.db.execute(
                    "UPDATE fact_tender SET parent_tender_id = ? WHERE tender_id = ?",
                    (parent, child),
                )
