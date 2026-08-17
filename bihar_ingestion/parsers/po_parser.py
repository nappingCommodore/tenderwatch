"""Parse archived purchase-order payloads into fact_purchase_order.

Also enriches dim_vendor from the PO templateMap (po_vendor_details) and
captures any PO document attachments into fact_document (source='po').
"""

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
    sane_epoch,
)
from ._base import BaseParser
from .documents import extract_documents
from .fields import collect_field_values

# Vendor detail fields (by field code) inside the PO templateMap section
# "po_vendor_details" -> canonical dim_vendor columns.
_VENDOR_FIELD_MAP = {
    "vendor_org": "name",
    "vendor_code": "vendor_code",
    "v_legacy_code": "legacy_code",
    "vendor_gstin": "gstin",
    "uid_type": "uid_type",
    "uid": "uid",
    "address": "address",
    "city": "city",
    "region": "state",
    "country": "country",
}

# Line-item fields (by field code) inside the "po_rc_qi" (WORK DETAILS) groups.
_ITEM_FIELD_MAP = {
    "item_code": "item_code",
    "item_name": "item_name",
    "uom": "uom",
    "item_qty": "quantity",
    "unit_price_rate": "unit_price_rate",
    "sub_total": "sub_total",
    "totaltax": "total_tax",
    "total_cost": "total_cost",
    "remarks": "remarks",
}


def _iter_item_groups(payload: Any) -> list[dict]:
    """Return the po_rc_qi line-item group objects from a PO's templateMap.

    Scoped to ``templateMap`` (the data instances); the schema-only ``templates``
    definitions are ignored so we never overwrite values with nulls.
    """

    template_map = payload.get("templateMap")
    if not isinstance(template_map, dict):
        return []
    items: list[dict] = []
    for groups in template_map.values():
        if not isinstance(groups, list):
            continue
        for grp in groups:
            if (
                isinstance(grp, dict)
                and grp.get("shortName") == "po_rc_qi"
                and isinstance(grp.get("templateFieldList"), list)
            ):
                items.append(grp)
    return items


class PurchaseOrderParser(BaseParser):
    """Populates fact_purchase_order, dim_vendor, and PO documents."""

    def __init__(self, db: Database, base_url: str) -> None:
        super().__init__(db)
        self.base_url = base_url

    def run(self) -> dict[str, int]:
        now = now_iso()
        po_count = 0
        vendor_count = 0
        doc_count = 0
        item_count = 0
        for raw in self.db.iter_latest_raw("po"):
            payload = json.loads(raw["payload"])
            for po in self._iter_po_objects(payload):
                if self._parse_po(po, raw["id"], now):
                    po_count += 1
                    vendor_count += int(self._enrich_vendor(po, now))
                    doc_count += self._parse_documents(po, raw["id"], now)
                    item_count += self._parse_items(po, raw["id"], now)
            self.db.commit()
        return {
            "purchase_orders": po_count,
            "vendors_enriched": vendor_count,
            "po_documents": doc_count,
            "po_items": item_count,
        }

    @staticmethod
    def _iter_po_objects(payload: Any) -> list[dict]:
        if isinstance(payload, dict) and payload.get("epspoId") is not None:
            return [payload]
        if isinstance(payload, list):
            return [p for p in payload if isinstance(p, dict) and p.get("epspoId")]
        return []

    def _parse_po(self, p: dict, raw_id: int, now: str) -> bool:
        po_id = coerce_int(p.get("epspoId"))
        tender_id = coerce_int(p.get("tenderId"))
        if po_id is None or tender_id is None:
            return False

        vendor_id = coerce_int(p.get("vendorId"))
        currency = clean_text(p.get("quoteCurrency"))
        self.ensure_tender_stub(tender_id, now)
        self.ensure_vendor(vendor_id)
        self.ensure_currency(currency)
        self.ensure_status("po", coerce_int(p.get("poStatus")))

        row = {
            "po_id": po_id,
            "org_po_id": coerce_int(p.get("orgpoId")),
            "legacy_po_number": clean_text(p.get("legacyPoNumber")),
            "tender_id": tender_id,
            "vendor_id": vendor_id,
            "po_type": clean_text(p.get("poType")),
            "po_ref": clean_text(p.get("poRef")),
            "po_value": coerce_float(p.get("poValue")),
            "currency": currency,
            "item_count": coerce_int(p.get("itemCount")),
            "is_rate_contract": clean_text(p.get("isRateContract")),
            "status_code": coerce_int(p.get("poStatus")),
            "quote_ref_no": clean_text(p.get("quoteRefNo")),
            "parent_po_id": coerce_int(p.get("parentPoId")),
            "amend_serial_no": coerce_int(p.get("ammendSerialNo")),
            "creation_epoch": sane_epoch(p.get("poCreationDate")),
            "creation_date": epoch_ms_to_iso(p.get("poCreationDate")),
            "start_epoch": sane_epoch(p.get("poStartDt")),
            "start_date": epoch_ms_to_iso(p.get("poStartDt")),
            "expiry_epoch": sane_epoch(p.get("poExpiryDt")),
            "expiry_date": epoch_ms_to_iso(p.get("poExpiryDt")),
            "bid_submission_epoch": sane_epoch(p.get("bidsubmissiondate")),
            "bid_submission_date": epoch_ms_to_iso(p.get("bidsubmissiondate")),
            "raw_response_id": raw_id,
        }
        self.upsert_with_seen("fact_purchase_order", row, pk="po_id", now=now)
        return True

    def _enrich_vendor(self, p: dict, now: str) -> bool:
        """Fill dim_vendor from the PO's po_vendor_details fields."""

        vendor_id = coerce_int(p.get("vendorId"))
        if vendor_id is None:
            return False
        values = collect_field_values(p, _VENDOR_FIELD_MAP.keys())
        attrs = {
            col: clean_text(values.get(code))
            for code, col in _VENDOR_FIELD_MAP.items()
            if clean_text(values.get(code)) is not None
        }
        if not attrs:
            return False
        # Only overwrite columns we actually resolved (keep existing otherwise).
        sets = ", ".join(f"{col} = ?" for col in attrs)
        params = list(attrs.values()) + [vendor_id]
        self.db.execute(f"UPDATE dim_vendor SET {sets} WHERE vendor_id = ?", params)
        return True

    def _parse_documents(self, p: dict, raw_id: int, now: str) -> int:
        tender_id = coerce_int(p.get("tenderId"))
        if tender_id is None:
            return 0
        docs = extract_documents(p, self.base_url)
        for doc in docs:
            self.upsert_document(doc, tender_id=tender_id, source="po",
                                 raw_id=raw_id, now=now)
        return len(docs)

    def _parse_items(self, p: dict, raw_id: int, now: str) -> int:
        po_id = coerce_int(p.get("epspoId"))
        tender_id = coerce_int(p.get("tenderId"))
        count = 0
        for serial, grp in enumerate(_iter_item_groups(p), start=1):
            values = collect_field_values(grp, _ITEM_FIELD_MAP.keys())
            row = {
                "po_id": po_id,
                "tender_id": tender_id,
                "serial_no": coerce_int(grp.get("indexSerialNo")) or serial,
                "table_primary_key_id": coerce_int(grp.get("tablePrimaryKeyId")),
                "item_code": clean_text(values.get("item_code")) or clean_text(grp.get("itemCode")),
                "item_name": clean_text(values.get("item_name")),
                "uom": clean_text(values.get("uom")),
                "quantity": coerce_float(values.get("item_qty")) or coerce_float(grp.get("itemQty")),
                "unit_price_rate": coerce_float(values.get("unit_price_rate")),
                "sub_total": coerce_float(values.get("sub_total")),
                "total_tax": coerce_float(values.get("totaltax")),
                "total_cost": coerce_float(values.get("total_cost")),
                "remarks": clean_text(values.get("remarks")),
                "raw_response_id": raw_id,
            }
            self._upsert_item(row, now)
            count += 1
        return count

    def _upsert_item(self, row: dict[str, Any], now: str) -> None:
        existing = self.db.scalar(
            "SELECT first_seen_at FROM fact_po_item WHERE po_id = ? AND serial_no = ?",
            (row["po_id"], row["serial_no"]),
        )
        row = dict(row)
        row["first_seen_at"] = existing or now
        row["last_seen_at"] = now
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(
            f"{c}=excluded.{c}" for c in cols if c not in {"po_item_id", "first_seen_at"}
        )
        sql = (
            f"INSERT INTO fact_po_item ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(po_id, serial_no) DO UPDATE SET {updates}"
        )
        self.db.execute(sql, tuple(row[c] for c in cols))
