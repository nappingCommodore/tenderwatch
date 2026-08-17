"""Parse Schedule-of-Rates (SOR) estimate line items from tender payloads.

The tender detail payload exposes a rate-contract grid (shortName
``br_rfq_item_rc``) carrying the *estimated* SOR rate per item. Comparing that
to the awarded ``unit_price_rate`` in fact_po_item (same tender_id + item_code)
gives a rate-vs-benchmark ratio that works even for lump-sum works, because it
compares like-for-like within an item rather than per-unit across items.

Scoped to tenders that actually have an award, so we read only the payloads we
can use instead of streaming all ~130k tender payloads.
"""

from __future__ import annotations

import json
from typing import Any

from ..utils import clean_text, coerce_float, now_iso
from ._base import BaseParser
from .fields import collect_field_values

# br_rfq_item_rc field code -> fact_sor_item column.
_SOR_FIELD_MAP = {
    "item_code": "item_code",
    "item_name": "item_name",
    "uom": "uom",
    "item_qty": "quantity",
    "sor_rate": "sor_rate",
    "estimat_price": "estimated_price",
    "mandatory_item": "mandatory",
}


def _iter_sor_groups(payload: Any) -> list[dict]:
    """Return the br_rfq_item_rc groups (the SOR estimate grid) from a tender."""

    groups: list[dict] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if (
                node.get("shortName") == "br_rfq_item_rc"
                and isinstance(node.get("templateFieldList"), list)
            ):
                groups.append(node)
            for value in node.values():
                walk(value)

    walk(payload)
    return groups


class SorItemParser(BaseParser):
    """Populates fact_sor_item from the tender rate-contract grid."""

    def run(self) -> dict[str, int]:
        now = now_iso()
        tender_ids = [
            row["tender_id"]
            for row in self.db.query(
                "SELECT DISTINCT tender_id FROM fact_purchase_order ORDER BY tender_id"
            )
        ]
        total = len(tender_ids)
        print(f"[sor] extracting SOR items for {total} awarded tenders...", flush=True)
        item_count = 0
        tender_count = 0
        for i, tid in enumerate(tender_ids, 1):
            raw = self.db.query(
                "SELECT id, payload FROM raw_response "
                "WHERE entity_type = 'tender' AND entity_key = ? "
                "ORDER BY id DESC LIMIT 1",
                (str(tid),),
            )
            if raw:
                payload = json.loads(raw[0]["payload"])
                got = self._parse_tender(payload, tid, raw[0]["id"], now)
                item_count += got
                tender_count += int(got > 0)
            if i % 2000 == 0 or i == total:
                self.db.commit()
                print(f"  [sor] {i}/{total} ({i / total * 100:.1f}%)", flush=True)
        self.db.commit()
        return {"sor_tenders": tender_count, "sor_items": item_count}

    def _parse_tender(self, payload: Any, tender_id: int, raw_id: int, now: str) -> int:
        self.ensure_tender_stub(tender_id, now)
        count = 0
        seen_codes: set[str] = set()
        for grp in _iter_sor_groups(payload):
            values = collect_field_values(grp, _SOR_FIELD_MAP.keys())
            item_code = clean_text(values.get("item_code")) or clean_text(grp.get("itemCode"))
            if item_code is None or item_code in seen_codes:
                continue
            seen_codes.add(item_code)
            row = {
                "tender_id": tender_id,
                "item_code": item_code,
                "item_name": clean_text(values.get("item_name")),
                "uom": clean_text(values.get("uom")),
                "quantity": coerce_float(values.get("item_qty")),
                "sor_rate": coerce_float(values.get("sor_rate")),
                "estimated_price": coerce_float(values.get("estimat_price")),
                "mandatory": clean_text(values.get("mandatory_item")),
                "raw_response_id": raw_id,
            }
            self._upsert_sor(row, now)
            count += 1
        return count

    def _upsert_sor(self, row: dict[str, Any], now: str) -> None:
        existing = self.db.scalar(
            "SELECT first_seen_at FROM fact_sor_item WHERE tender_id = ? AND item_code = ?",
            (row["tender_id"], row["item_code"]),
        )
        row = dict(row)
        row["first_seen_at"] = existing or now
        row["last_seen_at"] = now
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(
            f"{c}=excluded.{c}" for c in cols if c not in {"sor_item_id", "first_seen_at"}
        )
        sql = (
            f"INSERT INTO fact_sor_item ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(tender_id, item_code) DO UPDATE SET {updates}"
        )
        self.db.execute(sql, tuple(row[c] for c in cols))
