"""Aggregate analytics: award timeline, by-district rollup, capture leaderboard."""

import json
from pathlib import Path

import altair as alt
import pandas as pd
import plotly.express as px
import streamlit as st

from lib.db import q
from lib.geo import DISTRICTS, district_of

GEOJSON = Path(__file__).resolve().parents[2] / "data" / "geo" / "bihar_districts.geojson"

st.set_page_config(page_title="Aggregates", layout="wide")
from lib import ui as _ui  # noqa: E402
_ui.inject()
st.title("Aggregates & Trends")

# ---- award timeline ----------------------------------------------------------
st.subheader("Awards over time (trusted value)")
st.caption("Watch for fiscal year-end (March) spikes.")
tl = q(
    "SELECT substr(p.creation_date, 1, 7) AS ym, COUNT(*) AS n, "
    "       SUM(tr.trusted_po_value) AS value "
    "FROM fact_purchase_order p JOIN v_po_value_trusted tr ON tr.po_id = p.po_id "
    "WHERE p.creation_date IS NOT NULL AND p.creation_date <> '' "
    "GROUP BY 1 ORDER BY 1"
)
if len(tl):
    tl = tl.copy()
    tl["value_cr"] = tl["value"] / 1e7
    tl["is_march"] = tl["ym"].str.endswith("-03")
    st.altair_chart(
        alt.Chart(tl).mark_bar().encode(
            x=alt.X("ym:N", title="month"),
            y=alt.Y("value_cr:Q", title="Rs Cr"),
            color=alt.Color("is_march:N", title="March (FY-end)",
                            scale=alt.Scale(range=["#4c78a8", "#e45756"])),
            tooltip=["ym", "n", alt.Tooltip("value_cr:Q", format=",.1f")],
        ).properties(height=260),
        width="stretch",
    )

st.divider()

# ---- by district -------------------------------------------------------------
st.subheader("By district")
st.caption("Department names mapped (best-effort) to Bihar's 38 districts.")
depts = q("SELECT dept_id, dept_name, dept_value, dept_awards FROM v_dept_vendor_concentration")
flags = q("SELECT dept_id, COUNT(*) AS n_flags FROM fact_anomaly_flag "
          "WHERE dept_id IS NOT NULL GROUP BY dept_id")
depts = depts.merge(flags, on="dept_id", how="left")
depts["n_flags"] = depts["n_flags"].fillna(0)
depts["district"] = depts["dept_name"].map(district_of)
unmapped = int(depts["district"].isna().sum())

agg = (
    depts.dropna(subset=["district"]).groupby("district")
    .agg(value=("dept_value", "sum"), awards=("dept_awards", "sum"),
         flags=("n_flags", "sum"), depts=("dept_id", "count"))
    .reset_index()
)
# include every district (0 where no mapped awards) so all 38 render
byd = pd.DataFrame({"district": DISTRICTS}).merge(agg, on="district", how="left").fillna(0)
byd["value_cr"] = byd["value"] / 1e7

metric_label = {"value_cr": "Awarded Rs Cr", "awards": "# awards", "flags": "# flags"}
metric = st.radio("Colour by", list(metric_label), horizontal=True,
                  format_func=lambda m: metric_label[m])

col1, col2 = st.columns([3, 2])
with col1:
    if GEOJSON.exists():
        gj = json.loads(GEOJSON.read_text(encoding="utf-8"))
        fig = px.choropleth(
            byd, geojson=gj, featureidkey="properties.district",
            locations="district", color=metric, color_continuous_scale="Reds",
            hover_name="district", hover_data=["value_cr", "awards", "flags", "depts"],
        )
        fig.update_geos(fitbounds="locations", visible=False)
        fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=560,
                          coloraxis_colorbar_title=metric_label[metric])
        st.plotly_chart(fig, width="stretch")
    else:
        st.altair_chart(
            alt.Chart(byd.sort_values(metric, ascending=False).head(25)).mark_bar().encode(
                x=alt.X(f"{metric}:Q", title=metric_label[metric]),
                y=alt.Y("district:N", sort="-x", title=None),
                tooltip=["district", "value_cr", "awards", "flags"],
            ).properties(height=560), width="stretch")
with col2:
    st.dataframe(
        byd.sort_values(metric, ascending=False)[
            ["district", "value_cr", "awards", "flags", "depts"]],
        width="stretch", hide_index=True, height=560)
st.caption(f"{unmapped} departments couldn't be matched to a district.")

st.divider()

# ---- capture leaderboard -----------------------------------------------------
st.subheader("Vendor-capture leaderboard")
cap = q(
    "SELECT dept_name, dept_awards, n_vendors, ROUND(top_vendor_share * 100, 1) AS top_pct, "
    "       ROUND(hhi, 2) AS hhi, dept_value "
    "FROM v_flag_vendor_capture ORDER BY dept_value DESC LIMIT 30"
)
st.dataframe(cap, width="stretch", hide_index=True)
