"""Read-only data access for the web app over the Bihar eProc SQLite DB.

Reuses the materialized ``mv_*`` snapshot tables (built by
``Database.materialize_views``) transparently: heavy analytics views are
rewritten to their snapshot table when present, so page loads read precomputed
results in milliseconds instead of recomputing multi-second aggregations.
"""

from __future__ import annotations

import os
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

# Path to the SQLite DB. Override with the BIHAR_DB env var (the Docker / HF
# deployment points this at the slim data/bihar_web.db built by
# scripts/build_web_db.py); defaults to the full pipeline DB for local dev.
_DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "bihar_eproc.db"
DB_PATH = Path(os.environ.get("BIHAR_DB") or _DEFAULT_DB)

# Heavy views snapshotted into physical mv_ tables (longest name first so no
# name is a prefix of another during substitution).
_MATERIALIZED = sorted(
    (
        "v_po_value_trusted",
        "v_flag_vendor_capture",
        "v_vendor_concentration",
        "v_dept_vendor_concentration",
        "v_district_vendor_concentration",
        "v_flag_tender_splitting",
        "v_flag_award_vs_estimate",
        "v_flag_sor_overprice",
        "v_officer_concentration",
        "v_vendor_related_party",
        "v_graph_vendor_dept",
    ),
    key=len,
    reverse=True,
)


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


@lru_cache(maxsize=1)
def _snapshots() -> frozenset[str]:
    con = _connect()
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'mv\\_%' ESCAPE '\\'"
        ).fetchall()
    finally:
        con.close()
    return frozenset(r[0] for r in rows)


def _prefer_snapshots(sql: str) -> str:
    present = _snapshots()
    for view in _MATERIALIZED:
        mv = "mv_" + view[2:]
        if mv in present:
            sql = re.sub(rf"\b{view}\b", mv, sql)
    return sql


def query(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    """Run a read-only query and return a list of row dicts."""
    con = _connect()
    try:
        cur = con.execute(_prefer_snapshots(sql), tuple(params))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        con.close()


def one(sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def scalar(sql: str, params: tuple | list = ()) -> Any:
    row = one(sql, params)
    return next(iter(row.values())) if row else None


def update_flag(flag_id: int, status: str, note: str | None, reviewer: str | None) -> None:
    """Persist an analyst review decision (the one write path)."""
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("PRAGMA busy_timeout = 30000")
        con.execute(
            "UPDATE fact_anomaly_flag "
            "SET status = ?, review_note = ?, reviewed_by = ?, reviewed_at = datetime('now') "
            "WHERE flag_id = ?",
            (status, note or None, reviewer or None, int(flag_id)),
        )
        con.commit()
    finally:
        con.close()


# -- formatting helpers -------------------------------------------------------

def rupees(x: float | None) -> str:
    if x is None:
        return "-"
    x = float(x)
    if abs(x) >= 1e7:
        return f"₹{x / 1e7:,.2f} Cr"
    if abs(x) >= 1e5:
        return f"₹{x / 1e5:,.2f} L"
    return f"₹{x:,.0f}"


def cr(x: float | None) -> str:
    return f"₹{float(x or 0) / 1e7:,.1f} Cr"


def num(x: float | None) -> str:
    return f"{int(x or 0):,}"
