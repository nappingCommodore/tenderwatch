"""Vendor explorer: profile, flags, concentration, related parties, ego-graph."""

import altair as alt
import streamlit as st

from lib import viz
from lib.db import q, rupees

st.set_page_config(page_title="Vendors", layout="wide")
from lib import ui as _ui  # noqa: E402
_ui.inject()
st.title("Vendor Explorer")

search = st.text_input("Search vendor name", value=st.session_state.get("vendor_search", ""))
matches = q(
    "SELECT vendor_id, name, total_value FROM v_vendor_concentration "
    "WHERE name LIKE ? ORDER BY total_value DESC LIMIT 200",
    (f"%{search}%",),
) if search else q(
    "SELECT vendor_id, name, total_value FROM v_vendor_concentration "
    "ORDER BY total_value DESC LIMIT 200"
)
if not len(matches):
    st.info("No vendors match.")
    st.stop()

labels = {int(r.vendor_id): f"{r.name} ({rupees(r.total_value)})" for r in matches.itertuples()}
vid = st.selectbox("Vendor", list(labels), format_func=lambda i: labels[i])

prof = q("SELECT * FROM v_vendor_concentration WHERE vendor_id = ?", (int(vid),))
if len(prof):
    p = prof.iloc[0]
    st.subheader(p["name"] or f"Vendor {vid}")
    c = st.columns(5)
    c[0].metric("Awards", int(p["awards"]))
    c[1].metric("Trusted value", rupees(p["total_value"]))
    c[2].metric("Departments", int(p["n_departments"]))
    c[3].metric("Top-dept share", f"{100 * (p['top_dept_share'] or 0):.0f}%")
    c[4].metric("PAN", p["pan"] or "-")
    st.caption(f"{p['city'] or ''} {p['state'] or ''}".strip() or " ")

st.divider()
left, right = st.columns([1, 1])
with left:
    st.subheader("Departments supplied")
    depts = q(
        "SELECT COALESCE(d.name,'dept '||vd.dept_id) AS dept, vd.total_value AS value, vd.n_awards "
        "FROM v_graph_vendor_dept vd LEFT JOIN dim_department d ON d.department_id = vd.dept_id "
        "WHERE vd.vendor_id = ? ORDER BY vd.total_value DESC LIMIT 15",
        (int(vid),),
    )
    if len(depts):
        st.altair_chart(
            alt.Chart(depts).mark_bar().encode(
                x=alt.X("value:Q", title="trusted Rs"),
                y=alt.Y("dept:N", sort="-x", title=None),
                tooltip=["dept", "value", "n_awards"],
            ).properties(height=320),
            width="stretch",
        )
with right:
    st.subheader("Flags")
    flags = q(
        "SELECT score, rule_code, status, tender_id, evidence FROM v_anomaly_worklist "
        "WHERE vendor_id = ? ORDER BY score DESC",
        (int(vid),),
    )
    if len(flags):
        st.dataframe(flags, width="stretch", hide_index=True, height=320)
    else:
        st.caption("No flags on this vendor.")

st.subheader("Related parties (shared PAN / GSTIN)")
rp = q(
    "SELECT name_1, name_2, shared_pan, shared_gstin FROM v_vendor_related_party "
    "WHERE vendor_id_1 = ? OR vendor_id_2 = ?",
    (int(vid), int(vid)),
)
if len(rp):
    st.dataframe(rp, width="stretch", hide_index=True)
else:
    st.caption("None found.")

st.subheader("Ego network (vendor ↔ departments)")
viz.render(viz.ego_html("vendor", int(vid)))
