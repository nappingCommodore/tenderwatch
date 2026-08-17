"""Network view: related-party clusters (shared PAN/GSTIN) + vendor capture."""

import networkx as nx
import pandas as pd
import streamlit as st

from lib.db import q, rupees

st.set_page_config(page_title="Network", layout="wide")
from lib import ui as _ui  # noqa: E402
_ui.inject()
st.title("Network & Related Parties")

tab_rp, tab_cap = st.tabs(["Related parties (shared PAN / GSTIN)", "Vendor capture"])

with tab_rp:
    pairs = q("SELECT vendor_id_1, name_1, vendor_id_2, name_2, shared_pan, shared_gstin "
              "FROM v_vendor_related_party")
    if not len(pairs):
        st.info("No related-party pairs found.")
    else:
        g = nx.Graph()
        names: dict[int, str] = {}
        for r in pairs.itertuples():
            g.add_edge(int(r.vendor_id_1), int(r.vendor_id_2))
            names[int(r.vendor_id_1)] = r.name_1 or str(r.vendor_id_1)
            names[int(r.vendor_id_2)] = r.name_2 or str(r.vendor_id_2)
        clusters = sorted((sorted(c) for c in nx.connected_components(g)),
                          key=len, reverse=True)
        st.caption(f"{len(pairs)} pairs → **{len(clusters)}** clusters "
                   "(distinct vendor records that are the same/related entity).")

        rows = [{"cluster": i, "vendors": len(c),
                 "names": " · ".join(names.get(v, str(v)) for v in c)}
                for i, c in enumerate(clusters, 1)]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=300)

        sel = st.selectbox("Inspect cluster", range(1, len(clusters) + 1))
        members = clusters[sel - 1]
        ph = ",".join("?" * len(members))
        detail = q(
            f"SELECT vendor_id, name, pan, total_value, awards, n_departments "
            f"FROM v_vendor_concentration WHERE vendor_id IN ({ph})",
            tuple(members),
        )
        st.dataframe(detail, width="stretch", hide_index=True)
        combined = detail["total_value"].fillna(0).sum() if len(detail) else 0
        st.metric("Combined awarded value", rupees(combined))

        shared = q(
            f"SELECT dept_id, COUNT(DISTINCT vendor_id) AS nv "
            f"FROM v_graph_vendor_dept WHERE vendor_id IN ({ph}) "
            f"GROUP BY dept_id HAVING nv > 1",
            tuple(members),
        )
        if len(shared):
            st.warning(f"⚠️ These vendors jointly won in **{len(shared)}** department(s) — "
                       "possible award-splitting under one identity.")

with tab_cap:
    st.subheader("Vendor-capture leaderboard")
    st.caption("Departments where one vendor took ≥ 60% of ≥ 5 awards.")
    cap = q(
        "SELECT dept_name, dept_awards, n_vendors, "
        "       ROUND(top_vendor_share * 100, 1) AS top_pct, ROUND(hhi, 2) AS hhi, dept_value "
        "FROM v_flag_vendor_capture ORDER BY dept_value DESC"
    )
    st.dataframe(cap, width="stretch", hide_index=True, height=460)
