"""Extract issuing/approving authority identities into dim_authority.

The tender detail payload carries full authority objects (name, designation,
org, email, contact) for both the issuing and approving authority. We only
stored the numeric ids on fact_tender; this resolves them to names so the
dashboard shows people/offices instead of numbers.

Scoped to awarded tenders (which drive the officer-vendor analysis); the same
authorities recur across tenders, so this captures nearly all of them.
"""

from __future__ import annotations

import json
from typing import Any

from ..utils import clean_text, coerce_int, now_iso
from ._base import BaseParser

# (role, container key, id field, name field, designation field). Issuing and
# approving are SEPARATE id spaces that collide numerically, so role is part of
# the key -- never merge them.
_AUTH_OBJECTS = [
    ("issuing", "tenderIssuingAuthority", "tenderIssuingAuthorityId",
     "tenderIssuingAuthorityName", "tenderIssuingAuthorityDesignation"),
    ("approving", "tenderApprovingAuthority", "tenderApprovingAuthorityId",
     "tenderApprovingAuthorityName", "tenderApprovingAuthorityDesignation"),
]


def _find_authorities(payload: Any) -> list[tuple[str, dict, int | None, str, str]]:
    """Return (role, obj, id, name_field, desig_field) for every authority object."""
    out: list[tuple[str, dict, int | None, str, str]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for role, key, idf, namef, desf in _AUTH_OBJECTS:
                obj = node.get(key)
                if isinstance(obj, dict) and obj.get(idf) is not None:
                    out.append((role, obj, coerce_int(obj.get(idf)), namef, desf))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return out


class AuthorityParser(BaseParser):
    """Populates dim_authority from tender issuing/approving authority objects."""

    def run(self) -> dict[str, int]:
        tender_ids = [
            r["tender_id"]
            for r in self.db.query(
                "SELECT DISTINCT tender_id FROM fact_purchase_order ORDER BY tender_id"
            )
        ]
        total = len(tender_ids)
        print(f"[authorities] scanning {total} awarded tenders...", flush=True)
        seen: set[tuple[str, int]] = set()
        for i, tid in enumerate(tender_ids, 1):
            raw = self.db.query(
                "SELECT payload FROM raw_response "
                "WHERE entity_type = 'tender' AND entity_key = ? "
                "ORDER BY id DESC LIMIT 1",
                (str(tid),),
            )
            if raw:
                payload = json.loads(raw[0]["payload"])
                for role, obj, aid, namef, desf in _find_authorities(payload):
                    if aid is None or (role, aid) in seen:
                        continue
                    seen.add((role, aid))
                    self._upsert(role, aid, obj, namef, desf)
            if i % 2000 == 0 or i == total:
                self.db.commit()
                print(f"  [authorities] {i}/{total} ({len(seen)} found)", flush=True)
        self.db.commit()
        return {"authorities": len(seen)}

    def _upsert(self, role: str, aid: int, obj: dict, namef: str, desf: str) -> None:
        row = {
            "role": role,
            "authority_id": aid,
            "name": clean_text(obj.get(namef)),
            "designation": clean_text(obj.get(desf)),
            "org_name": clean_text(obj.get("organizationName")),
            "org_code": clean_text(obj.get("organizationCode")),
            "email": clean_text(obj.get("email")),
            "contact_no": clean_text(obj.get("contactNo")),
            "address": clean_text(obj.get("address")),
            "parent_id": coerce_int(obj.get("parentId")),
            "is_active": coerce_int(obj.get("isActive")),
        }
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols
                            if c not in ("role", "authority_id"))
        self.db.execute(
            f"INSERT INTO dim_authority ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(role, authority_id) DO UPDATE SET {updates}",
            tuple(row.values()),
        )
