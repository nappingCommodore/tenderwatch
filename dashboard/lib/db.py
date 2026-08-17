"""Read-only queries + review write-back over the Bihar eProc SQLite DB."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "bihar_eproc.db"

# Heavy views that the backend snapshots into physical ``mv_`` tables
# (Database.materialize_views). When those snapshots exist we transparently
# rewrite the query to read them, turning multi-second aggregations into
# millisecond table scans. Ordered longest-first so no name is a prefix of
# another during substitution.
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


@st.cache_data(ttl=600, show_spinner=False)
def _snapshots() -> frozenset[str]:
    """Names of the mv_ snapshot tables currently present in the DB."""
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
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


@st.cache_data(ttl=300, show_spinner=False)
def q(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Run a read-only query and return a DataFrame (cached).

    Heavy analytics views are transparently redirected to their ``mv_``
    snapshot tables when available (see materialize_views on the backend).
    """
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        return pd.read_sql_query(_prefer_snapshots(sql), con, params=params)
    finally:
        con.close()


def scalar(sql: str, params: tuple = ()):
    df = q(sql, params)
    return df.iloc[0, 0] if len(df) else None


def update_flag(flag_id: int, status: str, note: str, reviewer: str) -> None:
    """Persist an analyst review decision, then clear the read cache."""
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("PRAGMA busy_timeout = 30000")
        con.execute(
            "UPDATE fact_anomaly_flag "
            "SET status = ?, review_note = ?, reviewed_by = ?, "
            "    reviewed_at = datetime('now') "
            "WHERE flag_id = ?",
            (status, note or None, reviewer or None, int(flag_id)),
        )
        con.commit()
    finally:
        con.close()
    q.clear()


def rupees(x: float | None) -> str:
    """Format a rupee amount as Cr / Lakh for compact display."""
    if x is None:
        return "-"
    x = float(x)
    if abs(x) >= 1e7:
        return f"Rs {x / 1e7:,.2f} Cr"
    if abs(x) >= 1e5:
        return f"Rs {x / 1e5:,.2f} L"
    return f"Rs {x:,.0f}"


@st.cache_data(ttl=600, show_spinner=False)
def orgid_map() -> dict:
    """internal tender_id -> portal-facing 'Tender/RFQ ID' (orgtenderid)."""
    df = q("SELECT tender_id, org_tender_id FROM fact_tender WHERE org_tender_id IS NOT NULL")
    return dict(zip(df["tender_id"].tolist(), df["org_tender_id"].tolist()))


def orgid(tid):
    """Public Tender/RFQ ID for an internal tender_id (falls back to the id)."""
    if tid is None or (isinstance(tid, float) and pd.isna(tid)):
        return tid
    return orgid_map().get(int(tid), int(tid))
