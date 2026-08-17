"""Parse archived corrigendum payloads into fact_corrigendum."""

from __future__ import annotations

import json
from typing import Any

from ..db import Database
from ..utils import (
    clean_text,
    coerce_int,
    epoch_ms_to_iso,
    now_iso,
    sane_epoch,
)
from ._base import BaseParser


class CorrigendumParser(BaseParser):
    """Populates fact_corrigendum from corrigendum payloads."""

    def run(self) -> dict[str, int]:
        now = now_iso()
        count = 0
        for raw in self.db.iter_latest_raw("corrigendum"):
            payload = json.loads(raw["payload"])
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                if self._parse_corrigendum(item, raw["id"], now):
                    count += 1
            self.db.commit()
        return {"corrigenda": count}

    def _parse_corrigendum(self, c: Any, raw_id: int, now: str) -> bool:
        if not isinstance(c, dict):
            return False
        corr_id = coerce_int(c.get("corrId"))
        tender_id = coerce_int(c.get("tenderid"))
        if corr_id is None or tender_id is None:
            return False

        self.ensure_tender_stub(tender_id, now)
        self.ensure_status("corrigendum", coerce_int(c.get("status")))

        row = {
            "corrigendum_id": corr_id,
            "org_corrigendum_id": coerce_int(c.get("orgcorrigendumid")),
            "tender_id": tender_id,
            "group_id": coerce_int(c.get("groupid")),
            "version_no": coerce_int(c.get("versionno")),
            "ref_no": clean_text(c.get("corrRefNo")),
            "description": clean_text(c.get("corrDesc")),
            "status_code": coerce_int(c.get("status")),
            "template_group_ids": clean_text(c.get("templategroupids")),
            "attach_file": clean_text(c.get("attachFile")),
            "file_name": clean_text(c.get("fileName")),
            "bidder_modification_required": clean_text(c.get("bidderModificationRequired")),
            "create_epoch": sane_epoch(c.get("createdate")),
            "create_date": epoch_ms_to_iso(c.get("createdate")),
            "update_epoch": sane_epoch(c.get("updatedate")),
            "update_date": epoch_ms_to_iso(c.get("updatedate")),
            "raw_response_id": raw_id,
        }
        self.upsert_with_seen("fact_corrigendum", row, pk="corrigendum_id", now=now)
        return True
