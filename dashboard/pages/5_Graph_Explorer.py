"""Graph Explorer: render only a focused ego-network, never the global graph."""

import streamlit as st

from lib import viz
from lib.db import q, rupees

st.set_page_config(page_title="Graph Explorer", layout="wide")
from lib import ui as _ui  # noqa: E402
_ui.inject()
st.title("Graph Explorer")
st.caption("Focused ego-networks only — pick one vendor or department and see its "
           "immediate award neighbourhood. The full 17k-node graph is never rendered.")

focus_type = st.radio("Focus on", ["vendor", "department"], horizontal=True)
top_n = st.slider("Max neighbours", 10, 100, 40, 5)

if focus_type == "vendor":
    opts = q("SELECT vendor_id AS id, name, total_value AS v FROM v_vendor_concentration "
             "ORDER BY total_value DESC LIMIT 300")
else:
    opts = q("SELECT dept_id AS id, dept_name AS name, dept_value AS v "
             "FROM v_dept_vendor_concentration ORDER BY dept_value DESC LIMIT 300")

labels = {int(r.id): f"{r.name} ({rupees(r.v)})" for r in opts.itertuples()}
fid = st.selectbox(focus_type.title(), list(labels), format_func=lambda i: labels[i])

viz.render(viz.ego_html(focus_type, int(fid), top_n=top_n), height=660)
st.caption("Node size ≈ trusted value · edge thickness ≈ award value · red = vendor, blue = department.")
