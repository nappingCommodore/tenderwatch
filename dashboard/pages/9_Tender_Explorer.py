"""Tender Explorer: search / browse individual tenders, then open in Case Detail."""

import streamlit as st

from lib.db import q, orgid

st.set_page_config(page_title="Tender Explorer", layout="wide")
from lib import ui as _ui  # noqa: E402
_ui.inject()
st.title("Tender Explorer")
st.caption("Search and browse individual tenders, then open one in Case Detail.")

cats = q("SELECT proc_cat_id, description FROM dim_procurement_category ORDER BY description")
cat_opts = {int(r.proc_cat_id): (r.description or f"category {int(r.proc_cat_id)}")
            for r in cats.itertuples()}

with st.sidebar:
    st.header("Filters")
    text = st.text_input("Description / ref-no contains")
    dept_q = st.text_input("Department contains")
    vendor_q = st.text_input("Awarded vendor contains")
    sel_cats = st.multiselect("Category", list(cat_opts), format_func=lambda i: cat_opts[i])
    min_est = st.number_input("Min estimate (Rs Cr)", min_value=0.0, value=0.0, step=1.0)
    only_flagged = st.checkbox("Only tenders with flags")
    sort = st.selectbox("Sort by", ["Newest", "Highest estimate", "Most flags"])
    limit = st.slider("Max results", 50, 1000, 300, 50)

where, params = ["1=1"], []
if text:
    where.append("(t.description LIKE ? OR t.ref_no LIKE ?)")
    params += [f"%{text}%", f"%{text}%"]
if dept_q:
    where.append("t.dept_id IN (SELECT department_id FROM dim_department WHERE name LIKE ?)")
    params += [f"%{dept_q}%"]
if sel_cats:
    where.append("t.proc_cat_id IN (%s)" % ",".join("?" * len(sel_cats)))
    params += sel_cats
if min_est and min_est > 0:
    where.append("t.pac_amount >= ?")
    params += [min_est * 1e7]
if vendor_q:
    where.append(
        "EXISTS (SELECT 1 FROM fact_purchase_order p JOIN dim_vendor v "
        "ON v.vendor_id = p.vendor_id WHERE p.tender_id = t.tender_id AND v.name LIKE ?)"
    )
    params += [f"%{vendor_q}%"]
if only_flagged:
    where.append("EXISTS (SELECT 1 FROM fact_anomaly_flag f WHERE f.tender_id = t.tender_id)")

clause = " AND ".join(where)
base_order = "t.pac_amount DESC" if sort == "Highest estimate" else "t.publish_epoch DESC"

sql = f"""
WITH base AS (
    SELECT t.tender_id, t.org_tender_id, t.ref_no, t.description, t.dept_id, t.proc_cat_id,
           t.pac_amount, t.publish_date, t.publish_epoch
    FROM fact_tender t
    WHERE {clause}
    ORDER BY {base_order}
    LIMIT ?
)
SELECT b.tender_id, b.org_tender_id, b.ref_no, substr(b.description, 1, 70) AS description,
       d.name AS department, pc.description AS category,
       b.pac_amount AS estimate, substr(b.publish_date, 1, 10) AS published,
       (SELECT COALESCE(SUM(tr.trusted_po_value), 0) FROM fact_purchase_order p
          JOIN v_po_value_trusted tr ON tr.po_id = p.po_id
          WHERE p.tender_id = b.tender_id)                            AS awarded,
       (SELECT v.name FROM fact_purchase_order p JOIN dim_vendor v
          ON v.vendor_id = p.vendor_id WHERE p.tender_id = b.tender_id LIMIT 1) AS vendor,
       (SELECT COUNT(*) FROM fact_anomaly_flag f WHERE f.tender_id = b.tender_id) AS n_flags
FROM base b
LEFT JOIN dim_department d ON d.department_id = b.dept_id
LEFT JOIN dim_procurement_category pc ON pc.proc_cat_id = b.proc_cat_id
"""
df = q(sql, tuple(params + [limit]))

if sort == "Most flags":
    df = df.sort_values("n_flags", ascending=False)
elif sort == "Highest estimate":
    df = df.sort_values("estimate", ascending=False)

st.caption(f"**{len(df)}** tenders (capped at {limit}). Refine with the filters on the left.")

show = df.copy()
show["est_Cr"] = (show["estimate"].fillna(0) / 1e7).round(2)
show["award_Cr"] = (show["awarded"].fillna(0) / 1e7).round(2)
show["rfq_id"] = show["org_tender_id"].fillna(show["tender_id"]).astype("Int64")
st.dataframe(
    show[["rfq_id", "ref_no", "description", "department", "category",
          "est_Cr", "award_Cr", "vendor", "published", "n_flags"]],
    width="stretch", hide_index=True, height=460,
)

st.divider()
st.subheader("Open a tender")
if len(df):
    tid = st.selectbox("Tender/RFQ ID", df["tender_id"].tolist(),
                       format_func=lambda i: str(orgid(i)))
    st.session_state["case_tender_id"] = int(orgid(tid))
    try:
        st.page_link("pages/2_Case_Detail.py",
                     label=f"Open tender {int(orgid(tid))} in Case Detail", icon="\U0001f50e")
    except Exception:
        st.caption(f"Open the **Case Detail** page (sidebar) to inspect tender {int(orgid(tid))}.")
