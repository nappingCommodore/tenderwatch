"""Data-quality validation checks over the canonical database."""

from __future__ import annotations

from dataclasses import dataclass

from .db import Database


@dataclass
class Check:
    name: str
    severity: str          # "error" | "warn" | "info"
    count: int
    detail: str


class Validator:
    """Runs a battery of integrity / quality checks and reports the results."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def run(self) -> list[Check]:
        checks: list[Check] = []
        checks.extend(self._foreign_key_checks())
        checks.extend(self._reconciliation())
        checks.extend(self._completeness())
        return checks

    # -- referential integrity --------------------------------------------
    def _foreign_key_checks(self) -> list[Check]:
        violations = self.db.query("PRAGMA foreign_key_check")
        out = [
            Check(
                "foreign_key_integrity",
                "error" if violations else "info",
                len(violations),
                "PRAGMA foreign_key_check violations"
                + (f" in: {', '.join(sorted({v[0] for v in violations}))}" if violations else ""),
            )
        ]
        orphan_po = self.db.scalar(
            "SELECT COUNT(*) FROM fact_purchase_order po "
            "LEFT JOIN fact_tender t ON t.tender_id = po.tender_id "
            "WHERE t.tender_id IS NULL"
        )
        out.append(Check("po_without_tender", "error" if orphan_po else "info",
                         orphan_po or 0, "purchase orders with no parent tender"))
        orphan_doc = self.db.scalar(
            "SELECT COUNT(*) FROM fact_document d "
            "LEFT JOIN fact_tender t ON t.tender_id = d.tender_id "
            "WHERE t.tender_id IS NULL"
        )
        out.append(Check("document_without_tender", "error" if orphan_doc else "info",
                         orphan_doc or 0, "documents with no parent tender"))
        orphan_item = self.db.scalar(
            "SELECT COUNT(*) FROM fact_po_item i "
            "LEFT JOIN fact_purchase_order po ON po.po_id = i.po_id "
            "WHERE po.po_id IS NULL"
        )
        out.append(Check("po_item_without_po", "error" if orphan_item else "info",
                         orphan_item or 0, "PO line items with no parent PO"))
        return out

    # -- reconciliation ----------------------------------------------------
    def _reconciliation(self) -> list[Check]:
        discovered = self.db.scalar("SELECT COUNT(*) FROM tender_discovery") or 0
        detailed = self.db.scalar(
            "SELECT COUNT(*) FROM tender_discovery WHERE detail_fetched = 1"
        ) or 0
        parsed = self.db.scalar("SELECT COUNT(*) FROM fact_tender") or 0
        stub = self.db.scalar(
            "SELECT COUNT(*) FROM fact_tender WHERE ref_no IS NULL"
        ) or 0
        missing = max(detailed - parsed, 0)
        return [
            Check("tenders_discovered", "info", discovered, "rows in tender_discovery"),
            Check("tenders_detail_fetched", "info", detailed, "details fetched"),
            Check("tenders_parsed", "info", parsed, "rows in fact_tender"),
            Check("tender_stubs", "warn" if stub else "info", stub,
                  "fact_tender rows lacking detail (stubs from PO/corrigendum)"),
            Check("detailed_not_parsed", "warn" if missing else "info", missing,
                  "fetched details not represented in fact_tender"),
        ]

    # -- completeness ------------------------------------------------------
    def _completeness(self) -> list[Check]:
        dup_refs = self.db.scalar(
            "SELECT COUNT(*) FROM (SELECT ref_no FROM fact_tender "
            "WHERE ref_no IS NOT NULL GROUP BY ref_no HAVING COUNT(*) > 1)"
        ) or 0
        no_dept = self.db.scalar(
            "SELECT COUNT(*) FROM fact_tender WHERE dept_id IS NULL"
        ) or 0
        docs_no_url = self.db.scalar(
            "SELECT COUNT(*) FROM fact_document WHERE download_url IS NULL"
        ) or 0
        po_no_vendor = self.db.scalar(
            "SELECT COUNT(*) FROM fact_purchase_order WHERE vendor_id IS NULL"
        ) or 0
        return [
            Check("duplicate_ref_no", "warn" if dup_refs else "info", dup_refs,
                  "tender reference numbers shared by multiple tenders"),
            Check("tender_missing_department", "warn" if no_dept else "info", no_dept,
                  "tenders without a department id"),
            Check("documents_missing_url", "warn" if docs_no_url else "info", docs_no_url,
                  "documents without a download URL"),
            Check("po_missing_vendor", "info", po_no_vendor,
                  "purchase orders without a vendor id"),
        ]


def format_report(checks: list[Check]) -> str:
    lines = ["Validation report", "=" * 60]
    width = max((len(c.name) for c in checks), default=10)
    for c in checks:
        marker = {"error": "[X]", "warn": "[!]", "info": "[ ]"}.get(c.severity, "[ ]")
        lines.append(f"{marker} {c.name.ljust(width)}  {c.count:>8}  {c.detail}")
    errors = sum(1 for c in checks if c.severity == "error" and c.count)
    warns = sum(1 for c in checks if c.severity == "warn" and c.count)
    lines.append("-" * 60)
    lines.append(f"errors: {errors}   warnings: {warns}")
    return "\n".join(lines)
