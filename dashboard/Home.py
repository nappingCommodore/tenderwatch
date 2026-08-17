"""Bihar Procurement Integrity Monitor -- consolidated, editorial overview of
every red-flag signal, in the style of a forensic procurement report."""

import streamlit as st

from lib import ui
from lib.db import q

st.set_page_config(page_title="Bihar Procurement Integrity Monitor", layout="wide")
ui.inject()


def _cr(x):
    return f"Rs {float(x or 0) / 1e7:,.1f} Cr"


def _frac(series):
    m = float(series.max()) if len(series) and series.max() else 1.0
    return [float(v or 0) / m for v in series]


# ---- masthead ---------------------------------------------------------------
k = q("""
    SELECT (SELECT COUNT(*) FROM fact_tender)                                 AS tenders,
           (SELECT COUNT(*) FROM fact_purchase_order)                         AS awards,
           (SELECT COALESCE(SUM(trusted_po_value),0) FROM v_po_value_trusted) AS value,
           (SELECT COUNT(*) FROM fact_anomaly_flag)                           AS flags,
           (SELECT COUNT(*) FROM fact_anomaly_flag WHERE status='open')       AS open_flags
""").iloc[0]
ui.masthead(
    "Public procurement · red-flag analytics",
    "Bihar Procurement Integrity Monitor",
    "A forensic read of the Bihar e-Procurement record — vendor capture, "
    "overpricing against the Schedule of Rates, contract splitting and data "
    "corruption across the full awarded register.",
    [
        (f"{int(k['tenders']):,}", "Tenders analysed"),
        (f"{int(k['awards']):,}", "Awards"),
        (_cr(k["value"]), "Trusted award value"),
        (f"{int(k['flags']):,}", "Signals flagged"),
        (f"{int(k['open_flags']):,}", "Open for review"),
    ],
)

# ---- 01 vendor capture ------------------------------------------------------
n_cap = int(q("SELECT COUNT(*) n FROM v_flag_vendor_capture").iloc[0]["n"])
cap = q("SELECT dept_name, top_vendor_share, dept_value FROM v_flag_vendor_capture "
        "ORDER BY dept_value DESC LIMIT 10")
ui.section("01 · VENDOR CAPTURE",
           "Departments where one vendor takes the lion's share",
           f"{n_cap} departments let a single vendor win at least 60% of their awarded "
           "value. A wall of near-full bars is competition on paper only.")
ui.bars([(r.dept_name or "?",
          f"{100 * (r.top_vendor_share or 0):.0f}%  ·  {_cr(r.dept_value)}",
          r.top_vendor_share or 0) for r in cap.itertuples()])
if len(cap):
    ui.note(f"{n_cap} departments concentrate 60% or more of their awarded value in one "
            "vendor — the recurring pattern, not any single row, is the signal.")

# ---- 02 largest vendors -----------------------------------------------------
ven = q("SELECT name, total_value, n_departments FROM v_vendor_concentration "
        "ORDER BY total_value DESC LIMIT 10")
ui.section("02 · VENDOR LANDSCAPE", "Largest vendors by trusted award value",
           "Aggregate awarded value after repairing corrupted amounts.")
ui.bars([(r.name or "?", f"{_cr(r.total_value)}  ·  {int(r.n_departments)} depts", f)
         for r, f in zip(ven.itertuples(), _frac(ven["total_value"]))])
if len(ven):
    ui.note(f"{ven.iloc[0]['name']} leads at {_cr(ven.iloc[0]['total_value'])} spread "
            f"across {int(ven.iloc[0]['n_departments'])} departments.")

# ---- 03 SOR overpricing -----------------------------------------------------
n_sor = int(q("SELECT COUNT(*) n FROM v_flag_sor_overprice").iloc[0]["n"])
sor = q("SELECT tender_id, item_name, rate_ratio FROM v_flag_sor_overprice "
        "ORDER BY rate_ratio DESC LIMIT 10")
mx = float(sor["rate_ratio"].max()) if len(sor) else 1.0
ui.section("03 · OVERPRICING", "Awarded rates far above the Schedule of Rates",
           f"{n_sor} line items were awarded at 2x or more the tender's own SOR estimate "
           "— the sharpest surviving cost signal.")
ui.bars([(f"T{int(r.tender_id)} · {(r.item_name or '')[:40]}",
          f"{r.rate_ratio:.0f}x SOR", (r.rate_ratio or 0) / mx) for r in sor.itertuples()])
if len(sor):
    ui.note(f"The sharpest line was awarded at {sor.iloc[0]['rate_ratio']:.0f}x the "
            "tender's own Schedule-of-Rates estimate.")

# ---- 04 tender splitting ----------------------------------------------------
n_spl = int(q("SELECT COUNT(*) n FROM v_flag_tender_splitting").iloc[0]["n"])
spl = q("""SELECT COALESCE(d.name,'?') dept, COALESCE(v.name,'?') vendor,
                  s.ym, s.n_awards, s.total_value
           FROM v_flag_tender_splitting s
           LEFT JOIN dim_department d ON d.department_id=s.dept_id
           LEFT JOIN dim_vendor v ON v.vendor_id=s.vendor_id
           ORDER BY s.n_awards DESC LIMIT 10""")
ui.section("04 · CONTRACT SPLITTING", "Many sub-1Cr awards to one vendor in one month",
           f"{n_spl} department-vendor-month clusters look like one job split into many "
           "small awards to stay under an approval tier.")
ui.bars([(f"{(r.dept or '')[:26]} -> {(r.vendor or '')[:20]} ({r.ym})",
          f"{int(r.n_awards)} awards · {_cr(r.total_value)}", f)
         for r, f in zip(spl.itertuples(), _frac(spl["n_awards"]))])
if len(spl):
    ui.note(f"{spl.iloc[0]['dept']} placed {int(spl.iloc[0]['n_awards'])} sub-1Cr awards "
            f"with {spl.iloc[0]['vendor']} in {spl.iloc[0]['ym']} — "
            f"{_cr(spl.iloc[0]['total_value'])} that never crossed one approval tier.")

# ---- 05 data integrity ------------------------------------------------------
art = int(q("SELECT COALESCE(SUM(n_flags),0) n FROM v_anomaly_summary "
            "WHERE family='artifact'").iloc[0]["n"])
ui.section("05 · DATA INTEGRITY", "Corrupted amounts quarantined, not deleted",
           f"{art} awarded values are internally impossible (a number written twice, a "
           "percentage line larger than its base). Raw values are preserved; analysis "
           "uses repaired figures.")

# ---- 06 geography -----------------------------------------------------------
geo = q("SELECT district, dist_value, dist_awards FROM v_district_vendor_concentration "
        "ORDER BY dist_value DESC LIMIT 10")
ui.section("06 · GEOGRAPHY", "Where the money concentrates, by district",
           "Total trusted award value routed through departments in each district.")
ui.bars([(r.district, f"{_cr(r.dist_value)}  ·  {int(r.dist_awards)} awards", f)
         for r, f in zip(geo.itertuples(), _frac(geo["dist_value"]))])
if len(geo):
    ui.note(f"{geo.iloc[0]['district']} routes the most award value of any district, "
            f"at {_cr(geo.iloc[0]['dist_value'])}.")

st.write("")
st.page_link("pages/1_Worklist.py", label="Open the full ranked worklist", icon=":material/flag:")
ui.disclaimer(
    "Indicators surface statistical patterns, not findings of wrongdoing. All monetary "
    "figures use trusted (repaired) values; corrupted source amounts are flagged and "
    "preserved for audit. Officer and authority names are resolved per role from tender payloads."
)
