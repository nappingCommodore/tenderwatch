"""Department explorer: capture metrics, vendor concentration, flags, ego-graph."""

import altair as alt
import streamlit as st

from lib import viz
from lib.db import q, rupees

st.set_page_config(page_title="Departments", layout="wide")
from lib import ui as _ui  # noqa: E402
_ui.inject()
st.title("Department Explorer")

search = st.text_input("Search department name")
matches = q(
    "SELECT dept_id, dept_name, dept_value FROM v_dept_vendor_concentration "
    "WHERE dept_name LIKE ? ORDER BY dept_value DESC LIMIT 200",
    (f"%{search}%",),
) if search else q(
    "SELECT dept_id, dept_name, dept_value FROM v_dept_vendor_concentration "
    "ORDER BY dept_value DESC LIMIT 200"
)
if not len(matches):
    st.info("No departments match.")
    st.stop()

labels = {int(r.dept_id): f"{r.dept_name} ({rupees(r.dept_value)})" for r in matches.itertuples()}
did = st.selectbox("Department", list(labels), format_func=lambda i: labels[i])

prof = q("SELECT * FROM v_dept_vendor_concentration WHERE dept_id = ?", (int(did),))
capture = q("SELECT 1 FROM v_flag_vendor_capture WHERE dept_id = ?", (int(did),))
if len(prof):
    p = prof.iloc[0]
    st.subheader(p["dept_name"] or f"Department {did}")
    if len(capture):
        st.warning("⚠️ Flagged for vendor capture (one vendor dominates awards).")
    c = st.columns(5)
    c[0].metric("Awards", int(p["dept_awards"]))
    c[1].metric("Value", rupees(p["dept_value"]))
    c[2].metric("Vendors", int(p["n_vendors"]))
    c[3].metric("Top-vendor share", f"{100 * (p['top_vendor_share'] or 0):.0f}%")
    c[4].metric("HHI", f"{p['hhi']:.2f}")

st.divider()
left, right = st.columns([1, 1])
with left:
    st.subheader("Vendors (by trusted value)")
    vendors = q(
        "SELECT COALESCE(v.name,'vendor '||vd.vendor_id) AS vendor, vd.total_value AS value, vd.n_awards "
        "FROM v_graph_vendor_dept vd LEFT JOIN dim_vendor v ON v.vendor_id = vd.vendor_id "
        "WHERE vd.dept_id = ? ORDER BY vd.total_value DESC LIMIT 15",
        (int(did),),
    )
    if len(vendors):
        st.altair_chart(
            alt.Chart(vendors).mark_bar().encode(
                x=alt.X("value:Q", title="trusted Rs"),
                y=alt.Y("vendor:N", sort="-x", title=None),
                tooltip=["vendor", "value", "n_awards"],
            ).properties(height=320),
            width="stretch",
        )
with right:
    st.subheader("Flags")
    flags = q(
        "SELECT score, rule_code, status, vendor_name, evidence FROM v_anomaly_worklist "
        "WHERE dept_id = ? ORDER BY score DESC",
        (int(did),),
    )
    if len(flags):
        st.dataframe(flags, width="stretch", hide_index=True, height=320)
    else:
        st.caption("No flags on this department.")

st.subheader("Ego network (department ↔ vendors)")
viz.render(viz.ego_html("department", int(did)))
