"""Officer (approving-authority) explorer: repeat-approver / vendor nexus."""

import altair as alt
import streamlit as st

from lib import viz
from lib.db import q, rupees

st.set_page_config(page_title="Officers", layout="wide")
from lib import ui as _ui  # noqa: E402
_ui.inject()
st.title("Officer (Approving Authority) Explorer")
st.caption("Approving authorities resolved to names from tender payloads. "
           "High single-vendor share = repeat-approver nexus.")

top = q(
    "SELECT oc.officer_id, a.name, a.designation, a.org_name, "
    "       oc.total_value, oc.n_awards, oc.n_vendors, oc.top_vendor_share "
    "FROM v_officer_concentration oc "
    "LEFT JOIN dim_authority a ON a.authority_id = oc.officer_id AND a.role = 'approving' "
    "ORDER BY oc.total_value DESC LIMIT 300"
)
if not len(top):
    st.info("No officer data available.")
    st.stop()


def _olabel(r):
    who = r.name or f"Authority #{int(r.officer_id)}"
    return (f"{who} — {rupees(r.total_value)}, {int(r.n_vendors)} vendors, "
            f"top {100 * (r.top_vendor_share or 0):.0f}%")


labels = {int(r.officer_id): _olabel(r) for r in top.itertuples()}
oid = st.selectbox("Officer", list(labels), format_func=lambda i: labels[i])

prof = q(
    "SELECT oc.*, a.name, a.designation, a.org_name, a.email, a.contact_no "
    "FROM v_officer_concentration oc LEFT JOIN dim_authority a ON a.authority_id = oc.officer_id AND a.role = 'approving' "
    "WHERE oc.officer_id = ?",
    (int(oid),),
)
if len(prof):
    p = prof.iloc[0]
    st.subheader(p["name"] or f"Authority #{int(oid)}")
    meta = " · ".join(x for x in [p["designation"], p["org_name"]] if x)
    if meta:
        st.caption(meta)
    c = st.columns(4)
    c[0].metric("Awards approved", int(p["n_awards"]))
    c[1].metric("Value", rupees(p["total_value"]))
    c[2].metric("Vendors", int(p["n_vendors"]))
    c[3].metric("Top-vendor share", f"{100 * (p['top_vendor_share'] or 0):.0f}%")

st.divider()
left, right = st.columns([1, 1])
with left:
    st.subheader("Vendors approved (by value)")
    vs = q(
        "SELECT COALESCE(v.name,'vendor '||ov.vendor_id) AS vendor, "
        "       ov.total_value AS value, ov.n_awards "
        "FROM v_officer_vendor ov LEFT JOIN dim_vendor v ON v.vendor_id = ov.vendor_id "
        "WHERE ov.officer_id = ? ORDER BY ov.total_value DESC LIMIT 20",
        (int(oid),),
    )
    if len(vs):
        st.altair_chart(
            alt.Chart(vs).mark_bar().encode(
                x=alt.X("value:Q", title="trusted Rs"),
                y=alt.Y("vendor:N", sort="-x", title=None),
                tooltip=["vendor", "value", "n_awards"],
            ).properties(height=340),
            width="stretch",
        )
with right:
    st.subheader("Flags on this officer's tenders")
    flags = q(
        "SELECT w.score, w.rule_code, w.status, w.vendor_name, w.evidence "
        "FROM v_anomaly_worklist w JOIN fact_tender t ON t.tender_id = w.tender_id "
        "WHERE t.approving_authority_id = ? ORDER BY w.score DESC LIMIT 100",
        (int(oid),),
    )
    if len(flags):
        st.dataframe(flags, width="stretch", hide_index=True, height=340)
    else:
        st.caption("No flags on this officer's tenders.")

st.subheader("Ego network (officer ↔ vendors)")
viz.render(viz.ego_html("officer", int(oid)))
