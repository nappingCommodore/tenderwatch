"""Ranked red-numbers worklist with filters and the review write-back."""

import streamlit as st

from lib.db import q, rupees, update_flag, orgid

st.set_page_config(page_title="Worklist", layout="wide")
from lib import ui as _ui  # noqa: E402
_ui.inject()
st.title("Red-Numbers Worklist")

rules = q("SELECT DISTINCT rule_code FROM fact_anomaly_flag ORDER BY 1")["rule_code"].tolist()

with st.sidebar:
    st.header("Filters")
    f_rules = st.multiselect("Rule", rules)
    f_family = st.multiselect("Family", ["artifact", "reasonableness", "network"])
    f_status = st.multiselect("Status", ["open", "confirmed", "dismissed"], default=["open"])
    f_disp = st.multiselect("Disposition", ["quarantine", "review"])
    min_score = st.slider("Min score", 0.0, 1.0, 0.0, 0.05)
    search = st.text_input("Vendor / department contains")

where, params = ["score >= ?"], [min_score]
if f_rules:
    where.append("rule_code IN (%s)" % ",".join("?" * len(f_rules)))
    params += f_rules
if f_family:
    where.append("family IN (%s)" % ",".join("?" * len(f_family)))
    params += f_family
if f_status:
    where.append("status IN (%s)" % ",".join("?" * len(f_status)))
    params += f_status
if f_disp:
    where.append("disposition IN (%s)" % ",".join("?" * len(f_disp)))
    params += f_disp
if search:
    where.append("(vendor_name LIKE ? OR dept_name LIKE ?)")
    params += [f"%{search}%", f"%{search}%"]

clause = " AND ".join(where)
df = q(
    f"""SELECT flag_id, score, rule_code, family, disposition, status, entity_type,
               tender_id, vendor_name, dept_name, raw_value, metric, evidence
        FROM v_anomaly_worklist WHERE {clause}""",
    tuple(params),
)

st.caption(f"**{len(df):,}** flags match (of the full worklist).")
df.insert(df.columns.get_loc("tender_id"), "tender_rfq", df["tender_id"].map(orgid))
st.dataframe(df, width="stretch", hide_index=True, height=380)

st.divider()
st.subheader("Review a flag")

if not len(df):
    st.info("No flags match the current filters.")
    st.stop()

fid = st.selectbox("Flag id", df["flag_id"].tolist())
flag = q("SELECT * FROM fact_anomaly_flag WHERE flag_id = ?", (int(fid),)).iloc[0]
ctx = q("SELECT vendor_name, dept_name, tender_desc FROM v_anomaly_worklist WHERE flag_id = ?",
        (int(fid),))
ctx = ctx.iloc[0] if len(ctx) else None

a, b = st.columns([2, 1])
with a:
    st.markdown(f"**{flag['rule_code']}** · `{flag['entity_type']}` · score **{flag['score']:.3f}** "
                f"(severity {flag['severity']:.2f} × confidence {flag['confidence']:.2f})")
    st.write(flag["evidence"])
    if ctx is not None:
        if ctx["vendor_name"]:
            st.write(f"Vendor: **{ctx['vendor_name']}**")
        if ctx["dept_name"]:
            st.write(f"Department: **{ctx['dept_name']}**")
        if ctx["tender_desc"]:
            st.write(f"Tender: {ctx['tender_desc']}")
    st.write(f"raw={rupees(flag['raw_value'])} · repaired={rupees(flag['repaired_value'])} "
             f"· ref={rupees(flag['ref_value'])} · metric={flag['metric']}")
    if flag["tender_id"]:
        _oid = int(orgid(flag["tender_id"]))
        st.session_state["case_tender_id"] = _oid
        try:
            st.page_link("pages/2_Case_Detail.py",
                         label=f"Open tender {_oid} in Case Detail", icon="\U0001f50e")
        except Exception:
            st.caption(f"Open **Case Detail** (sidebar) to inspect tender {_oid}.")

with b:
    with st.form("review"):
        options = ["open", "confirmed", "dismissed"]
        cur = flag["status"] if flag["status"] in options else "open"
        status = st.radio("Status", options, index=options.index(cur))
        note = st.text_area("Note", value=flag["review_note"] or "")
        reviewer = st.text_input("Reviewer", value=flag["reviewed_by"] or "analyst")
        if st.form_submit_button("Save review", type="primary"):
            update_flag(int(fid), status, note, reviewer)
            st.success("Saved.")
            st.rerun()
