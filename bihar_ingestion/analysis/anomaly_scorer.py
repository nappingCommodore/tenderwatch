"""Consolidate every detector into one ranked, evidence-tagged worklist.

Each detector view contributes rows to ``fact_anomaly_flag``. A run UPSERTS the
metrics/evidence for each (rule_code, entity_type, entity_id) but PRESERVES the
analyst-set ``status`` and original ``created_at`` -- so re-scoring never wipes
a review decision. The table holds EVERY flagged case (not a top-N); callers
rank/paginate with ORDER BY score.

Scoring (all tunable):
  score = severity * confidence
  * severity  in 0..1, derived from the rule's magnitude metric
  * confidence in 0..1, how sure the flag is a real anomaly vs noise
    (mathematically-provable artifacts high; heuristics lower)
"""

from __future__ import annotations

import math
from typing import Any, Callable

from ..db import Database
from ..utils import now_iso


def _log_sev(value: Any, cap: float) -> float:
    """log10-scaled severity in 0..1 (bigger magnitude -> closer to 1)."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return 0.0
    if x <= 1.0:
        return 0.0
    return min(1.0, math.log10(x) / cap)


def _award_sev(row: dict[str, Any]) -> float:
    ratio = row.get("metric")
    try:
        ratio = float(ratio)
    except (TypeError, ValueError):
        return 0.5
    if ratio <= 0:
        return 0.5
    if ratio >= 1:                      # overrun: award >> estimate
        return min(1.0, math.log10(ratio) / 2.5)
    return min(1.0, math.log10(1.0 / ratio) / 1.5)   # lowball: award << estimate


# Each spec yields uniform columns:
#   entity_type, entity_id, tender_id, vendor_id, dept_id,
#   raw_value, repaired_value, ref_value, metric, evidence
_SPECS: list[dict[str, Any]] = [
    {
        "rule_code": "A1_digit_doubled", "family": "artifact",
        "disposition": "quarantine", "confidence": 0.95,
        "severity": lambda r: _log_sev(r["raw_value"], 15),
        "sql": """
            SELECT 'po' AS entity_type, a.po_id AS entity_id, a.tender_id AS tender_id,
                   p.vendor_id AS vendor_id, t.dept_id AS dept_id,
                   a.raw_value AS raw_value, a.repaired_value AS repaired_value,
                   NULL AS ref_value, CAST(a.n_digits AS REAL) AS metric,
                   'digit-doubled: ' || printf('%.0f', a.raw_value)
                     || ' -> ' || printf('%.0f', a.repaired_value) AS evidence
            FROM v_artifact_digit_doubled a
            JOIN fact_purchase_order p ON p.po_id = a.po_id
            LEFT JOIN fact_tender t ON t.tender_id = a.tender_id
        """,
    },
    {
        "rule_code": "A2_pct_tax_line", "family": "artifact",
        "disposition": "quarantine", "confidence": 0.95,
        "severity": lambda r: _log_sev(r["raw_value"], 15),
        "sql": """
            SELECT 'po_item' AS entity_type, a.po_item_id AS entity_id, a.tender_id AS tender_id,
                   p.vendor_id AS vendor_id, t.dept_id AS dept_id,
                   a.tax_line_value AS raw_value, a.base_value AS repaired_value,
                   a.base_value AS ref_value,
                   a.tax_line_value / NULLIF(a.base_value, 0) AS metric,
                   'pct/tax line ' || printf('%.0f', a.tax_line_value)
                     || ' exceeds base ' || printf('%.0f', a.base_value) AS evidence
            FROM v_artifact_pct_tax_line a
            JOIN fact_purchase_order p ON p.po_id = a.po_id
            LEFT JOIN fact_tender t ON t.tender_id = a.tender_id
        """,
    },
    {
        "rule_code": "A3_scale_error", "family": "artifact",
        "disposition": "review", "confidence": 0.6,
        "severity": lambda r: 0.5,
        "sql": """
            SELECT 'po' AS entity_type, a.po_id AS entity_id, a.tender_id AS tender_id,
                   p.vendor_id AS vendor_id, t.dept_id AS dept_id,
                   a.raw_value AS raw_value, a.repaired_value AS repaired_value,
                   a.estimate AS ref_value, a.ratio AS metric,
                   'po_value ~' || printf('%.0f', a.ratio)
                     || 'x estimate (scale/paise error?)' AS evidence
            FROM v_artifact_scale_error a
            JOIN fact_purchase_order p ON p.po_id = a.po_id
            LEFT JOIN fact_tender t ON t.tender_id = a.tender_id
        """,
    },
    {
        "rule_code": "A4_repeated_digit", "family": "artifact",
        "disposition": "quarantine", "confidence": 0.8,
        "severity": lambda r: 0.6,
        "sql": """
            SELECT 'po' AS entity_type, a.po_id AS entity_id, a.tender_id AS tender_id,
                   p.vendor_id AS vendor_id, t.dept_id AS dept_id,
                   a.raw_value AS raw_value, NULL AS repaired_value, NULL AS ref_value,
                   NULL AS metric, 'all identical digits: ' || a.digits AS evidence
            FROM v_artifact_repeated_digit a
            JOIN fact_purchase_order p ON p.po_id = a.po_id
            LEFT JOIN fact_tender t ON t.tender_id = a.tender_id
        """,
    },
    {
        "rule_code": "A5_epoch_as_value", "family": "artifact",
        "disposition": "review", "confidence": 0.5,
        "severity": lambda r: 0.5,
        "sql": """
            SELECT 'po' AS entity_type, a.po_id AS entity_id, a.tender_id AS tender_id,
                   p.vendor_id AS vendor_id, t.dept_id AS dept_id,
                   a.raw_value AS raw_value, NULL AS repaired_value, NULL AS ref_value,
                   NULL AS metric, 'value in ms-epoch band (date as amount?)' AS evidence
            FROM v_artifact_epoch_value a
            JOIN fact_purchase_order p ON p.po_id = a.po_id
            LEFT JOIN fact_tender t ON t.tender_id = a.tender_id
        """,
    },
    {
        "rule_code": "SOR_overprice", "family": "reasonableness",
        "disposition": "review", "confidence": 0.8,
        "severity": lambda r: _log_sev(r["metric"], 2.5),
        "sql": """
            SELECT 'po_line' AS entity_type,
                   (o.po_id || '#' || o.item_code) AS entity_id, o.tender_id AS tender_id,
                   p.vendor_id AS vendor_id, t.dept_id AS dept_id,
                   o.awarded_rate AS raw_value, NULL AS repaired_value,
                   o.sor_rate AS ref_value, o.rate_ratio AS metric,
                   'awarded ' || printf('%.0f', o.awarded_rate)
                     || ' vs SOR ' || printf('%.0f', o.sor_rate)
                     || ' = ' || printf('%.1f', o.rate_ratio) || 'x' AS evidence
            FROM v_flag_sor_overprice o
            JOIN fact_purchase_order p ON p.po_id = o.po_id
            LEFT JOIN fact_tender t ON t.tender_id = o.tender_id
        """,
    },
    {
        "rule_code": "V3_award_vs_estimate", "family": "reasonableness",
        "disposition": "review", "confidence": 0.5,
        "severity": _award_sev,
        "sql": """
            SELECT 'po' AS entity_type, e.po_id AS entity_id, e.tender_id AS tender_id,
                   p.vendor_id AS vendor_id, e.dept_id AS dept_id,
                   e.trusted_po_value AS raw_value, NULL AS repaired_value,
                   e.estimate AS ref_value, e.ratio AS metric,
                   e.direction || ': award ' || printf('%.0f', e.trusted_po_value)
                     || ' vs estimate ' || printf('%.0f', e.estimate)
                     || ' = ' || printf('%.1f', e.ratio) || 'x' AS evidence
            FROM v_flag_award_vs_estimate e
            JOIN fact_purchase_order p ON p.po_id = e.po_id
        """,
    },
    {
        "rule_code": "N1_vendor_capture", "family": "network",
        "disposition": "review", "confidence": 0.5,
        "severity": lambda r: min(1.0, float(r["metric"] or 0.0)),
        "sql": """
            SELECT 'department' AS entity_type, c.dept_id AS entity_id, NULL AS tender_id,
                   (SELECT vd.vendor_id FROM v_graph_vendor_dept vd
                    WHERE vd.dept_id = c.dept_id ORDER BY vd.total_value DESC LIMIT 1) AS vendor_id,
                   c.dept_id AS dept_id,
                   c.top_vendor_value AS raw_value, NULL AS repaired_value,
                   c.dept_value AS ref_value, c.top_vendor_share AS metric,
                   'one vendor took ' || printf('%.0f', c.top_vendor_share * 100)
                     || '% of ' || c.dept_awards || ' awards (Rs '
                     || printf('%.1f', c.dept_value / 1e7) || ' Cr)' AS evidence
            FROM v_flag_vendor_capture c
        """,
    },
    {
        "rule_code": "N4_related_party", "family": "network",
        "disposition": "review", "confidence": 0.7,
        "severity": lambda r: 0.6,
        "sql": """
            SELECT 'vendor_pair' AS entity_type,
                   (r.vendor_id_1 || '-' || r.vendor_id_2) AS entity_id, NULL AS tender_id,
                   r.vendor_id_1 AS vendor_id, NULL AS dept_id,
                   NULL AS raw_value, NULL AS repaired_value, NULL AS ref_value, NULL AS metric,
                   'shared ' || COALESCE(r.shared_pan, r.shared_gstin)
                     || ': ' || r.name_1 || ' <-> ' || r.name_2 AS evidence
            FROM v_vendor_related_party r
        """,
    },
    {
        "rule_code": "P2_tender_splitting", "family": "network",
        "disposition": "review", "confidence": 0.4,
        "severity": lambda r: min(1.0, float(r["metric"] or 0) / 12.0),
        "sql": """
            SELECT 'dept_vendor_month' AS entity_type,
                   (s.dept_id || ':' || s.vendor_id || ':' || s.ym) AS entity_id,
                   NULL AS tender_id, s.vendor_id AS vendor_id, s.dept_id AS dept_id,
                   s.total_value AS raw_value, NULL AS repaired_value,
                   s.max_award AS ref_value, CAST(s.n_awards AS REAL) AS metric,
                   s.n_awards || ' sub-1Cr awards to one vendor in ' || s.ym
                     || ' totalling Rs ' || printf('%.1f', s.total_value / 1e7) || ' Cr'
                     || ' (possible splitting)' AS evidence
            FROM v_flag_tender_splitting s
        """,
    },
    {
        "rule_code": "N3_vendor_district_concentration", "family": "network",
        "disposition": "review", "confidence": 0.3,
        "severity": lambda r: min(1.0, float(r["metric"] or 0) / 0.25),
        "sql": """
            SELECT 'district' AS entity_type, c.district AS entity_id, NULL AS tender_id,
                   (SELECT vd.vendor_id FROM v_graph_vendor_district vd
                    WHERE vd.district = c.district ORDER BY vd.total_value DESC LIMIT 1) AS vendor_id,
                   NULL AS dept_id,
                   c.top_vendor_value AS raw_value, NULL AS repaired_value,
                   c.dist_value AS ref_value, c.top_vendor_share AS metric,
                   'top district vendor holds ' || printf('%.0f', c.top_vendor_share * 100)
                     || '% of ' || c.district || ' (' || c.dist_awards
                     || ' awards, Rs ' || printf('%.1f', c.dist_value / 1e7) || ' Cr across '
                     || c.n_vendors || ' vendors)' AS evidence
            FROM v_flag_vendor_district_capture c
        """,
    },
    {
        "rule_code": "S1_value_outlier", "family": "reasonableness",
        "disposition": "review", "confidence": 0.4,
        "severity": lambda r: _log_sev(r["metric"], 2.0),
        "sql": """
            SELECT 'po' AS entity_type, o.po_id AS entity_id, o.tender_id AS tender_id,
                   o.vendor_id AS vendor_id, o.dept_id AS dept_id,
                   o.val AS raw_value, NULL AS repaired_value,
                   o.median_val AS ref_value, o.x_median AS metric,
                   'top ' || printf('%.1f', (1 - o.pr) * 100) || '% of ' || pc.description
                     || ' by value: Rs ' || printf('%.2f', o.val / 1e7) || ' Cr = '
                     || printf('%.0f', o.x_median) || 'x category median' AS evidence
            FROM v_flag_value_outlier o
            LEFT JOIN dim_procurement_category pc ON pc.proc_cat_id = o.proc_cat_id
        """,
    },
]


class AnomalyScorer:
    """Materialise every detector's flags into fact_anomaly_flag."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def run(self) -> dict[str, int]:
        now = now_iso()
        counts: dict[str, int] = {}
        for spec in _SPECS:
            n = 0
            for raw in self.db.query(spec["sql"]):
                row = dict(raw)
                severity: Callable[[dict], float] = spec["severity"]
                sev = round(float(severity(row)), 4)
                conf = float(spec["confidence"])
                self._upsert(spec, row, sev, conf, round(sev * conf, 4), now)
                n += 1
            counts[spec["rule_code"]] = n
            self.db.commit()
        counts["total"] = sum(counts.values())
        return counts

    def _upsert(self, spec: dict, r: dict, sev: float, conf: float,
                score: float, now: str) -> None:
        row = {
            "rule_code": spec["rule_code"],
            "family": spec["family"],
            "entity_type": r["entity_type"],
            "entity_id": str(r["entity_id"]),
            "tender_id": r.get("tender_id"),
            "vendor_id": r.get("vendor_id"),
            "dept_id": r.get("dept_id"),
            "severity": sev,
            "confidence": conf,
            "score": score,
            "raw_value": r.get("raw_value"),
            "repaired_value": r.get("repaired_value"),
            "ref_value": r.get("ref_value"),
            "metric": r.get("metric"),
            "evidence": r.get("evidence"),
            "disposition": spec["disposition"],
        }
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        # On re-score: refresh metrics/evidence but keep status + created_at.
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols)
        sql = (
            f"INSERT INTO fact_anomaly_flag ({', '.join(cols)}, status, created_at) "
            f"VALUES ({placeholders}, 'open', ?) "
            f"ON CONFLICT(rule_code, entity_type, entity_id) DO UPDATE SET {updates}"
        )
        self.db.execute(sql, tuple(row[c] for c in cols) + (now,))
