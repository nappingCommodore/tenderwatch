"""Parse master payloads into dimension tables."""

from __future__ import annotations

import json
from typing import Any

from ..db import Database
from ..utils import clean_text, coerce_int


class MasterParser:
    """Populates dim_* tables from archived master payloads."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def run(self) -> dict[str, int]:
        counts = {
            "rfq_category": 0,
            "rfq_type": 0,
            "procurement_category": 0,
            "bid_part": 0,
            "department": 0,
        }
        for raw in self.db.iter_latest_raw("master"):
            payload = json.loads(raw["payload"])
            if not isinstance(payload, dict):
                continue
            counts["rfq_category"] += self._rfq_categories(payload.get("rfqCategoryList"))
            counts["rfq_type"] += self._rfq_types(payload.get("rfqTypeList"))
            counts["procurement_category"] += self._proc_cats(payload.get("procCatList"))
            counts["bid_part"] += self._bid_parts(payload.get("bidPartList"))

        counts["department"] = self._departments()
        self.db.commit()
        return counts

    # -- dimension loaders -------------------------------------------------
    def _rfq_categories(self, items: Any) -> int:
        if not isinstance(items, list):
            return 0
        n = 0
        for it in items:
            rid = coerce_int(it.get("rfqcategoryId"))
            if rid is None:
                continue
            self.db.upsert(
                "dim_rfq_category",
                {
                    "rfq_category_id": rid,
                    "code": clean_text(it.get("code")),
                    "description": clean_text(it.get("description")),
                },
                pk="rfq_category_id",
            )
            n += 1
        return n

    def _rfq_types(self, items: Any) -> int:
        if not isinstance(items, list):
            return 0
        n = 0
        for it in items:
            rid = coerce_int(it.get("rfqtypeId"))
            if rid is None:
                continue
            self.db.upsert(
                "dim_rfq_type",
                {
                    "rfq_type_id": rid,
                    "description": clean_text(it.get("description")),
                    "tender_type": clean_text(it.get("tenderType")),
                    "single_tender_flag": clean_text(it.get("singleTenderFlag")),
                },
                pk="rfq_type_id",
            )
            n += 1
        return n

    def _proc_cats(self, items: Any) -> int:
        if not isinstance(items, list):
            return 0
        n = 0
        for it in items:
            pid = coerce_int(it.get("proccatId"))
            if pid is None:
                continue
            self.db.upsert(
                "dim_procurement_category",
                {
                    "proc_cat_id": pid,
                    "description": clean_text(it.get("description")),
                    "parent_proc_cat_id": coerce_int(it.get("parentProccatId")),
                },
                pk="proc_cat_id",
            )
            n += 1
            n += self._proc_cats(it.get("childProcCatList"))
        return n

    def _bid_parts(self, items: Any) -> int:
        if not isinstance(items, list):
            return 0
        n = 0
        for it in items:
            bid = coerce_int(it.get("id"))
            if bid is None:
                continue
            self.db.upsert(
                "dim_bid_part",
                {
                    "bid_part_id": bid,
                    "bid_part_no": coerce_int(it.get("bidpartno")),
                    "code": clean_text(it.get("code")),
                    "description": clean_text(it.get("description")),
                    "max_bid_part_no": coerce_int(it.get("maxbidpartno")),
                },
                pk="bid_part_id",
            )
            n += 1
        return n

    def _departments(self) -> int:
        """Flatten the org hierarchy payloads into dim_department.

        Two passes keep the self-referencing FK valid: insert every org with a
        NULL parent first, then link parents that actually exist.
        """

        flat: dict[int, dict[str, Any]] = {}
        links: dict[int, int] = {}

        def walk(items: Any) -> None:
            if not isinstance(items, list):
                return
            for it in items:
                if not isinstance(it, dict):
                    continue
                oid = coerce_int(it.get("organizationId"))
                if oid is None:
                    continue
                flat[oid] = {
                    "department_id": oid,
                    "parent_id": None,
                    "name": clean_text(it.get("organizationName")),
                    "code": clean_text(it.get("organizationCode")),
                    "address": clean_text(it.get("address")),
                    "storage_path": clean_text(it.get("storagePath")),
                    "identify_string": clean_text(it.get("identifystring")),
                    "poc_name": clean_text(it.get("pocName")),
                    "poc_email": clean_text(it.get("pocEmail")),
                    "poc_phone": clean_text(it.get("pocPhoneNo")),
                    "is_active": coerce_int(it.get("isActive")),
                }
                parent = coerce_int(it.get("parentId"))
                if parent:
                    links[oid] = parent
                walk(it.get("childOrgList"))

        for raw in self.db.iter_latest_raw("department"):
            walk(json.loads(raw["payload"]))

        for row in flat.values():
            self.db.upsert("dim_department", row, pk="department_id")

        for child, parent in links.items():
            if parent in flat:
                self.db.execute(
                    "UPDATE dim_department SET parent_id = ? WHERE department_id = ?",
                    (parent, child),
                )
        return len(flat)
