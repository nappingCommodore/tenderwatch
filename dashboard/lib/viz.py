"""Focused ego-network rendering (pyvis) — small subgraphs only, never global."""

from __future__ import annotations

import streamlit.components.v1 as components
from pyvis.network import Network

from .db import q

_VENDOR_COLOR = "#c0392b"
_DEPT_COLOR = "#2980b9"


def _to_html(net: Network) -> str:
    # pyvis .save_graph() writes with the platform default encoding (cp1252 on
    # Windows) and crashes on non-latin names; generate_html() returns the
    # string directly, which we hand to components.html (unicode-safe).
    try:
        return net.generate_html(notebook=False)
    except TypeError:  # older/newer signature
        return net.generate_html()


def _new_net() -> Network:
    net = Network(height="600px", width="100%", directed=False,
                  cdn_resources="in_line", bgcolor="#ffffff", font_color="#222222")
    net.barnes_hut(gravity=-8000, spring_length=120)
    return net


def ego_html(focus_type: str, focus_id: int, top_n: int = 40) -> str:
    """Build the ego network of one vendor, department, or officer."""
    fid = int(focus_id)
    if focus_type == "vendor":
        rows = q(
            "SELECT vd.dept_id AS oid, COALESCE(d.name,'dept '||vd.dept_id) AS label, "
            "       vd.total_value AS val, vd.n_awards AS n "
            "FROM v_graph_vendor_dept vd LEFT JOIN dim_department d "
            "  ON d.department_id = vd.dept_id "
            "WHERE vd.vendor_id = ? ORDER BY vd.total_value DESC LIMIT ?",
            (fid, int(top_n)),
        )
        lab = q("SELECT COALESCE(name,'vendor '||vendor_id) AS l FROM dim_vendor WHERE vendor_id=?", (fid,))
        label = lab.iloc[0]["l"] if len(lab) else f"vendor {fid}"
        c_id, c_color, o_prefix, o_color = f"V:{fid}", _VENDOR_COLOR, "D:", _DEPT_COLOR
    elif focus_type == "department":
        rows = q(
            "SELECT vd.vendor_id AS oid, COALESCE(v.name,'vendor '||vd.vendor_id) AS label, "
            "       vd.total_value AS val, vd.n_awards AS n "
            "FROM v_graph_vendor_dept vd LEFT JOIN dim_vendor v "
            "  ON v.vendor_id = vd.vendor_id "
            "WHERE vd.dept_id = ? ORDER BY vd.total_value DESC LIMIT ?",
            (fid, int(top_n)),
        )
        lab = q("SELECT COALESCE(name,'dept '||department_id) AS l FROM dim_department WHERE department_id=?", (fid,))
        label = lab.iloc[0]["l"] if len(lab) else f"dept {fid}"
        c_id, c_color, o_prefix, o_color = f"D:{fid}", _DEPT_COLOR, "V:", _VENDOR_COLOR
    else:  # officer
        rows = q(
            "SELECT vd.vendor_id AS oid, COALESCE(v.name,'vendor '||vd.vendor_id) AS label, "
            "       vd.total_value AS val, vd.n_awards AS n "
            "FROM v_officer_vendor vd LEFT JOIN dim_vendor v "
            "  ON v.vendor_id = vd.vendor_id "
            "WHERE vd.officer_id = ? ORDER BY vd.total_value DESC LIMIT ?",
            (fid, int(top_n)),
        )
        lab = q("SELECT name FROM dim_authority WHERE authority_id=? AND role='approving'", (fid,))
        label = lab.iloc[0]["name"] if len(lab) and lab.iloc[0]["name"] else f"Authority #{fid}"
        c_id, c_color, o_prefix, o_color = f"O:{fid}", "#8e44ad", "V:", _VENDOR_COLOR

    net = _new_net()
    net.add_node(c_id, label=str(label)[:36], color=c_color, size=34, shape="dot")
    max_val = max((float(r.val or 0) for r in rows.itertuples()), default=1.0) or 1.0
    for r in rows.itertuples():
        nid = f"{o_prefix}{int(r.oid)}"
        size = 8 + 22 * (float(r.val or 0) / max_val)
        net.add_node(nid, label=str(r.label)[:32], color=o_color, size=size)
        net.add_edge(c_id, nid, value=float(r.val or 1),
                     title=f"Rs {float(r.val or 0)/1e7:,.2f} Cr / {int(r.n)} awards")
    return _to_html(net)


def render(html: str, height: int = 620) -> None:
    components.html(html, height=height, scrolling=True)
