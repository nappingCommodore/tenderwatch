"""Shared helpers for canonical parsers."""

from __future__ import annotations

from typing import Any

from ..db import Database


class BaseParser:
    """Provides FK-safe dimension stubbing and seen-timestamp upserts."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # -- dimension stubs (keep FK integrity for portal-wide references) ----
    def ensure_department(self, dept_id: int | None) -> None:
        if dept_id is not None:
            self.db.execute(
                "INSERT OR IGNORE INTO dim_department (department_id) VALUES (?)",
                (dept_id,),
            )

    def ensure_proc_cat(self, proc_cat_id: int | None) -> None:
        if proc_cat_id is not None:
            self.db.execute(
                "INSERT OR IGNORE INTO dim_procurement_category (proc_cat_id) VALUES (?)",
                (proc_cat_id,),
            )

    def ensure_rfq_type(self, rfq_type_id: int | None) -> None:
        if rfq_type_id is not None:
            self.db.execute(
                "INSERT OR IGNORE INTO dim_rfq_type (rfq_type_id) VALUES (?)",
                (rfq_type_id,),
            )

    def ensure_currency(self, code: str | None) -> None:
        if code:
            self.db.execute(
                "INSERT OR IGNORE INTO dim_currency (currency_code) VALUES (?)", (code,)
            )

    def ensure_vendor(self, vendor_id: int | None) -> None:
        if vendor_id is not None:
            self.db.execute(
                "INSERT OR IGNORE INTO dim_vendor (vendor_id) VALUES (?)", (vendor_id,)
            )

    def ensure_status(self, entity: str, status_code: int | None) -> None:
        if status_code is not None:
            self.db.execute(
                "INSERT OR IGNORE INTO dim_status (entity, status_code) VALUES (?, ?)",
                (entity, status_code),
            )

    def ensure_tender_stub(self, tender_id: int | None, now: str) -> None:
        """Guarantee a fact_tender row exists so dependent facts satisfy their FK.

        Used when a PO/corrigendum references a tender whose detail was not (yet)
        parsed. The stub carries only the id + seen timestamps; a later detail
        parse fills in the remaining columns via upsert.
        """

        if tender_id is None:
            return
        self.db.execute(
            "INSERT OR IGNORE INTO fact_tender (tender_id, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?)",
            (tender_id, now, now),
        )


    def upsert_with_seen(
        self, table: str, row: dict[str, Any], pk: str, now: str
    ) -> None:
        """Upsert a fact row, preserving first_seen_at and refreshing last_seen_at."""

        existing = self.db.scalar(
            f"SELECT first_seen_at FROM {table} WHERE {pk} = ?", (row[pk],)
        )
        row = dict(row)
        row["first_seen_at"] = existing or now
        row["last_seen_at"] = now
        self.db.upsert(table, row, pk=pk)

    def upsert_document(self, doc: dict[str, Any], tender_id: int, source: str,
                        raw_id: int, now: str) -> None:
        """Insert/update a fact_document row keyed by its natural unique key."""

        row = {
            "tender_id": tender_id,
            "source": source,
            "label": doc.get("label"),
            "field_code": doc.get("field_code"),
            "template_group_id": doc.get("template_group_id"),
            "filename": doc.get("filename"),
            "relative_path": doc.get("relative_path"),
            "download_url": doc.get("download_url"),
            "file_size_bytes": doc.get("file_size_bytes"),
            "mime_type": doc.get("mime_type"),
            "sha256": None,
            "downloaded_at": None,
            "raw_response_id": raw_id,
        }
        existing = self.db.scalar(
            """
            SELECT first_seen_at FROM fact_document
            WHERE tender_id = ? AND IFNULL(template_group_id, -1) = IFNULL(?, -1)
              AND IFNULL(filename, '') = IFNULL(?, '')
              AND IFNULL(relative_path, '') = IFNULL(?, '')
            """,
            (row["tender_id"], row["template_group_id"], row["filename"],
             row["relative_path"]),
        )
        row["first_seen_at"] = existing or now
        row["last_seen_at"] = now
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(
            f"{c}=excluded.{c}" for c in cols if c not in {"document_id", "first_seen_at"}
        )
        sql = (
            f"INSERT INTO fact_document ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(tender_id, template_group_id, filename, relative_path) "
            f"DO UPDATE SET {updates}"
        )
        self.db.execute(sql, tuple(row[c] for c in cols))
