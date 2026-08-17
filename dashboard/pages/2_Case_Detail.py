"""Case detail: one tender's PO(s), line items vs SOR, documents, and flags."""

import streamlit as st

from lib.db import q, rupees, orgid

st.set_page_config(page_title="Case Detail", layout="wide")
from lib import ui as _ui  # noqa: E402
_ui.inject()
st.title("Case Detail")

default = int(st.session_state.get("case_tender_id", 0) or 0)
tid = st.number_input("Tender/RFQ ID", min_value=0, value=default, step=1)
if not tid:
    st.info("Enter a tender id (or open one from the Worklist).")
    st.stop()

tender = q(
    "SELECT t.tender_id, t.org_tender_id, t.description, t.ref_no, t.pac_amount, t.publish_date, "
    "       t.listing_status, d.name AS dept, "
    "       ia.name AS issuing_authority, ia.designation AS issuing_desig, "
    "       aa.name AS approving_authority, aa.designation AS approving_desig "
    "FROM fact_tender t "
    "LEFT JOIN dim_department d ON d.department_id = t.dept_id "
    "LEFT JOIN dim_authority ia ON ia.authority_id = t.issuing_authority_id AND ia.role = 'issuing' "
    "LEFT JOIN dim_authority aa ON aa.authority_id = t.approving_authority_id AND aa.role = 'approving' "
    "WHERE t.org_tender_id = ? OR t.tender_id = ? "
    "ORDER BY CASE WHEN t.org_tender_id = ? THEN 0 ELSE 1 END LIMIT 1",
    (int(tid), int(tid), int(tid)),
)
if not len(tender):
    st.error("No tender with that id.")
    st.stop()
t = tender.iloc[0]
tid = int(t["tender_id"])  # the PO/line-item/flag/doc queries below key on the internal id

st.subheader(t["description"] or f"Tender {t['org_tender_id'] or tid}")
c = st.columns(4)
c[0].metric("Estimate (PAC)", rupees(t["pac_amount"]))
c[1].metric("Department", t["dept"] or "-")
c[2].metric("Status", t["listing_status"] or "-")
c[3].metric("Published", str(t["publish_date"] or "-")[:10])
st.caption(f"Tender/RFQ ID: {t['org_tender_id'] or t['tender_id']}  \u00b7  Ref: {t['ref_no'] or '-'}")
st.caption(
    f"Issuing authority: **{t['issuing_authority'] or '-'}** "
    f"({t['issuing_desig'] or '-'})  \u00b7  "
    f"Approving authority: **{t['approving_authority'] or '-'}** "
    f"({t['approving_desig'] or '-'})"
)

st.divider()
st.subheader("Awards (purchase orders)")
pos = q(
    "SELECT p.po_id, v.name AS vendor, p.po_value, tr.trusted_po_value, "
    "       tr.data_quality_flag, p.creation_date "
    "FROM fact_purchase_order p "
    "LEFT JOIN dim_vendor v ON v.vendor_id = p.vendor_id "
    "LEFT JOIN v_po_value_trusted tr ON tr.po_id = p.po_id "
    "WHERE p.tender_id = ?",
    (int(tid),),
)
st.dataframe(pos, width="stretch", hide_index=True)

st.subheader("Line items — awarded vs Schedule of Rates (SOR)")
items = q(
    "SELECT i.serial_no, i.item_name, i.quantity, i.unit_price_rate AS awarded_rate, "
    "       s.sor_rate, "
    "       CASE WHEN s.sor_rate > 0 THEN ROUND(i.unit_price_rate / s.sor_rate, 1) END AS x_sor, "
    "       i.total_cost "
    "FROM fact_po_item i "
    "LEFT JOIN fact_sor_item s ON s.tender_id = i.tender_id AND s.item_code = i.item_code "
    "WHERE i.tender_id = ? ORDER BY i.serial_no",
    (int(tid),),
)
if len(items):
    st.dataframe(items, width="stretch", hide_index=True)
else:
    st.caption("No itemised line items for this tender.")

st.divider()
d1, d2 = st.columns(2)
with d1:
    st.subheader("Flags on this case")
    flags = q(
        "SELECT score, rule_code, status, evidence FROM v_anomaly_worklist "
        "WHERE tender_id = ? ORDER BY score DESC",
        (int(tid),),
    )
    if len(flags):
        st.dataframe(flags, width="stretch", hide_index=True)
    else:
        st.caption("No flags on this tender.")
with d2:
    st.subheader("Documents")
    docs = q(
        "SELECT label, filename, download_url FROM fact_document "
        "WHERE tender_id = ? LIMIT 200",
        (int(tid),),
    )
    if len(docs):
        st.dataframe(docs, width="stretch", hide_index=True)
    else:
        st.caption("No documents recorded.")
