"""FastAPI web app: a clean, professional surface over the Bihar eProc
anomaly dataset. Reads the same SQLite DB (+ mv_ snapshots) as the pipeline.

Run:  python -m uvicorn web.app:app --port 8000 --reload
"""

from __future__ import annotations

import csv
import io
import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import data
from .data import cr, num, query, rupees
from . import flagdocs
from . import casebook

ROOT = Path(__file__).resolve().parent
GEOJSON = ROOT.parents[0] / "data" / "geo" / "bihar_districts.geojson"

app = FastAPI(title="Bihar Procurement Integrity Monitor")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=str(ROOT / "templates"))
templates.env.filters["rupees"] = rupees
templates.env.filters["cr"] = cr
templates.env.filters["num"] = num


def _filesize(n):
    """Human-readable byte size; '—' when unknown."""
    if not n:
        return "—"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


templates.env.filters["filesize"] = _filesize
casebook.init()


@lru_cache(maxsize=1)
def _org_map() -> dict:
    """internal tender_id -> portal-facing 'Tender/RFQ ID' (orgtenderid).
    The portal lists tenders by orgtenderid; we key internally by tenderid."""
    return {r["tender_id"]: r["org_tender_id"]
            for r in query("SELECT tender_id, org_tender_id FROM fact_tender "
                           "WHERE org_tender_id IS NOT NULL")}


def _orgid(tender_id):
    if tender_id is None:
        return ""
    return _org_map().get(int(tender_id), tender_id)


templates.env.filters["orgid"] = _orgid


@lru_cache(maxsize=1)
def _totals() -> dict:
    r = data.one(
        "SELECT (SELECT COUNT(*) FROM fact_tender) AS tenders, "
        "(SELECT COUNT(*) FROM fact_anomaly_flag) AS flags"
    )
    return {"tenders": num(r["tenders"]), "flags": num(r["flags"])}


def render(request: Request, template: str, active: str, **kw) -> HTMLResponse:
    ac_id = request.cookies.get("active_case")
    acase = casebook.brief(int(ac_id)) if ac_id and ac_id.isdigit() else None
    cur = request.url.path + (("?" + request.url.query) if request.url.query else "")
    base = {"active": active,
            "kpi_tenders": _totals()["tenders"], "kpi_flags": _totals()["flags"],
            "flag_help": flagdocs.FLAG_DOCS, "active_case": acase, "cur_path": cur,
            "all_cases": casebook.listing()}
    base.update(kw)
    return templates.TemplateResponse(request=request, name=template, context=base)


def _frac(rows, key):
    mx = max((float(r[key] or 0) for r in rows), default=0.0) or 1.0
    return [float(r[key] or 0) / mx for r in rows]


# ============================================================ OVERVIEW
@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    k = data.one(
        "SELECT (SELECT COUNT(*) FROM fact_tender) AS tenders, "
        "(SELECT COUNT(*) FROM fact_purchase_order) AS awards, "
        "(SELECT COALESCE(SUM(trusted_po_value),0) FROM v_po_value_trusted) AS value, "
        "(SELECT COUNT(*) FROM fact_anomaly_flag) AS flags, "
        "(SELECT COUNT(*) FROM fact_anomaly_flag WHERE status='open') AS open_flags"
    )
    kpis = [
        (num(k["tenders"]), "Tenders analysed", False),
        (num(k["awards"]), "Awards", False),
        (cr(k["value"]), "Trusted value", False),
        (num(k["flags"]), "Signals flagged", True),
        (num(k["open_flags"]), "Open for review", False),
    ]

    sections = []

    cap = query("SELECT dept_id, dept_name, top_vendor_share, dept_value "
                "FROM v_flag_vendor_capture ORDER BY dept_value DESC LIMIT 10")
    n_cap = data.scalar("SELECT COUNT(*) FROM v_flag_vendor_capture")
    sections.append({
        "n": "01", "title": "Vendor capture", "cap": "One vendor takes most of a department's spend",
        "rows": [{"label": r["dept_name"] or "?", "href": f"/department/{r['dept_id']}",
                  "val": f"{100*(r['top_vendor_share'] or 0):.0f}% · {cr(r['dept_value'])}",
                  "frac": r["top_vendor_share"] or 0} for r in cap],
        "note": f"{n_cap} departments concentrate 60%+ of awarded value in a single vendor — "
                "the recurring pattern, not any one row, is the signal.",
    })

    ven = query("SELECT vendor_id, name, total_value, n_departments "
                "FROM v_vendor_concentration ORDER BY total_value DESC LIMIT 10")
    vf = _frac(ven, "total_value")
    sections.append({
        "n": "02", "title": "Vendor landscape", "cap": "Largest vendors by trusted award value",
        "rows": [{"label": r["name"] or "?", "href": f"/vendor/{r['vendor_id']}",
                  "val": f"{cr(r['total_value'])} · {int(r['n_departments'])} depts", "frac": f}
                 for r, f in zip(ven, vf)],
        "note": (f"{ven[0]['name']} leads at {cr(ven[0]['total_value'])} across "
                 f"{int(ven[0]['n_departments'])} departments." if ven else ""),
    })

    sor = query("SELECT tender_id, item_name, rate_ratio FROM v_flag_sor_overprice "
                "ORDER BY rate_ratio DESC LIMIT 10")
    sf = _frac(sor, "rate_ratio")
    sections.append({
        "n": "03", "title": "Overpricing", "cap": "Awarded rates far above the Schedule of Rates",
        "rows": [{"label": f"T{_orgid(r['tender_id'])} · {(r['item_name'] or '')[:38]}",
                  "href": f"/case/{_orgid(r['tender_id'])}", "val": f"{r['rate_ratio']:.0f}× SOR", "frac": f}
                 for r, f in zip(sor, sf)],
        "note": (f"The sharpest line was awarded at {sor[0]['rate_ratio']:.0f}× the tender's own "
                 "SOR estimate." if sor else ""),
    })

    spl = query("SELECT COALESCE(d.name,'?') dept, COALESCE(v.name,'?') vendor, s.ym, "
                "s.n_awards, s.total_value FROM v_flag_tender_splitting s "
                "LEFT JOIN dim_department d ON d.department_id=s.dept_id "
                "LEFT JOIN dim_vendor v ON v.vendor_id=s.vendor_id "
                "ORDER BY s.n_awards DESC LIMIT 10")
    pf = _frac(spl, "n_awards")
    sections.append({
        "n": "04", "title": "Contract splitting", "cap": "Many sub-1Cr awards to one vendor in one month",
        "rows": [{"label": f"{(r['dept'] or '')[:24]} → {(r['vendor'] or '')[:18]} ({r['ym']})",
                  "val": f"{int(r['n_awards'])} awards · {cr(r['total_value'])}", "frac": f}
                 for r, f in zip(spl, pf)],
        "note": (f"{spl[0]['dept']} placed {int(spl[0]['n_awards'])} sub-1Cr awards with "
                 f"{spl[0]['vendor']} in {spl[0]['ym']} — {cr(spl[0]['total_value'])} that never "
                 "crossed one approval tier." if spl else ""),
    })

    geo = query("SELECT district, dist_value, dist_awards FROM v_district_vendor_concentration "
                "ORDER BY dist_value DESC LIMIT 10")
    gf = _frac(geo, "dist_value")
    sections.append({
        "n": "05", "title": "Geography", "cap": "Where award value concentrates, by district",
        "rows": [{"label": r["district"], "val": f"{cr(r['dist_value'])} · {int(r['dist_awards'])} awards",
                  "frac": f} for r, f in zip(geo, gf)],
        "note": (f"{geo[0]['district']} routes the most award value of any district, at "
                 f"{cr(geo[0]['dist_value'])}." if geo else ""),
    })

    families = query("SELECT family, COUNT(*) AS n FROM fact_anomaly_flag GROUP BY family ORDER BY n DESC")
    by_rule = query("SELECT rule_code, n_flags FROM v_anomaly_summary ORDER BY n_flags DESC LIMIT 12")
    art = data.scalar("SELECT COALESCE(SUM(n_flags),0) FROM v_anomaly_summary WHERE family='artifact'")

    return render(request, "overview.html", "/",
                  kpis=kpis, sections=sections, artifact_count=num(art),
                  donut=[{"name": r["family"], "value": r["n"]} for r in families],
                  rulebar={"categories": [r["rule_code"] for r in by_rule][::-1],
                           "values": [r["n_flags"] for r in by_rule][::-1]})


# ============================================================ GUIDE
@app.get("/guide", response_class=HTMLResponse)
def guide(request: Request):
    counts = {r["rule_code"]: r for r in query(
        "SELECT rule_code, family, COUNT(*) AS n, ROUND(AVG(score),3) AS avg, "
        "ROUND(MAX(score),3) AS mx, MAX(confidence) AS conf, "
        "SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_n "
        "FROM fact_anomaly_flag GROUP BY rule_code")}
    groups = []
    for fam, codes in flagdocs.GROUPS:
        label, color, desc = flagdocs.FAMILY_DOCS[fam]
        flags = []
        for code in codes:
            c = counts.get(code, {})
            flags.append({"code": code, **flagdocs.FLAG_DOCS.get(code, {}),
                          "n": int(c.get("n") or 0), "avg": c.get("avg"), "max": c.get("mx"),
                          "conf": c.get("conf"), "open_n": int(c.get("open_n") or 0)})
        groups.append({"family": fam, "label": label, "color": color, "desc": desc, "flags": flags})
    total = data.scalar("SELECT COUNT(*) FROM fact_anomaly_flag")
    return render(request, "guide.html", "/guide",
                  workflow=flagdocs.WORKFLOW, score_notes=flagdocs.SCORE_NOTES,
                  playbook=flagdocs.PLAYBOOK, groups=groups, total_flags=num(total))


# ============================================================ WORKLIST
_FAMILIES = ["artifact", "reasonableness", "network"]
_STATUSES = ["open", "confirmed", "dismissed"]


@app.get("/investigations", response_class=HTMLResponse)
def investigations(request: Request, rule: str = "", family: str = "", status: str = "open",
                   min_score: float = 0.0, search: str = "", limit: int = 500):
    where, params = ["score >= ?"], [min_score]
    if rule:
        where.append("rule_code = ?"); params.append(rule)
    if family:
        where.append("family = ?"); params.append(family)
    if status:
        where.append("status = ?"); params.append(status)
    if search:
        where.append("(vendor_name LIKE ? OR dept_name LIKE ? OR evidence LIKE ?)")
        params += [f"%{search}%"] * 3
    clause = " AND ".join(where)
    rows = query(
        f"SELECT flag_id, score, rule_code, family, status, entity_type, tender_id, "
        f"vendor_name, dept_name, metric, evidence FROM v_anomaly_worklist "
        f"WHERE {clause} ORDER BY score DESC LIMIT ?", params + [limit])
    total = data.scalar(f"SELECT COUNT(*) FROM v_anomaly_worklist WHERE {clause}", params)
    rules = [r["rule_code"] for r in
             query("SELECT DISTINCT rule_code FROM fact_anomaly_flag ORDER BY 1")]
    return render(request, "investigations.html", "/investigations",
                  rows=rows, total=total, shown=len(rows), rules=rules,
                  families=_FAMILIES, statuses=_STATUSES,
                  f={"rule": rule, "family": family, "status": status,
                     "min_score": min_score, "search": search, "limit": limit})


# ============================================================ CASE DETAIL
@app.get("/case/{tid}", response_class=HTMLResponse)
def case(request: Request, tid: int, saved: int = 0):
    _sel = (
        "SELECT t.tender_id, t.org_tender_id, t.description, t.ref_no, t.pac_amount, t.publish_date, "
        "t.listing_status, t.dept_id, d.name AS dept, "
        "ia.name AS issuing_authority, ia.designation AS issuing_desig, t.issuing_authority_id, "
        "aa.name AS approving_authority, aa.designation AS approving_desig, t.approving_authority_id "
        "FROM fact_tender t LEFT JOIN dim_department d ON d.department_id=t.dept_id "
        "LEFT JOIN dim_authority ia ON ia.authority_id=t.issuing_authority_id AND ia.role='issuing' "
        "LEFT JOIN dim_authority aa ON aa.authority_id=t.approving_authority_id AND aa.role='approving' ")
    # Route by the public Tender/RFQ ID (orgtenderid); fall back to the internal
    # id for the one NULL-org tender and any pre-existing internal-id links.
    t = (data.one(_sel + "WHERE t.org_tender_id=? LIMIT 1", (tid,))
         or data.one(_sel + "WHERE t.tender_id=?", (tid,)))
    if not t:
        return render(request, "notfound.html", "/investigations", what=f"tender {tid}")
    tender_id = t["tender_id"]
    pos = query(
        "SELECT p.po_id, v.name AS vendor, p.vendor_id, p.po_value, tr.trusted_po_value, "
        "tr.data_quality_flag, p.creation_date FROM fact_purchase_order p "
        "LEFT JOIN dim_vendor v ON v.vendor_id=p.vendor_id "
        "LEFT JOIN v_po_value_trusted tr ON tr.po_id=p.po_id WHERE p.tender_id=?", (tender_id,))
    items = query(
        "SELECT i.serial_no, i.item_name, i.quantity, i.unit_price_rate AS awarded_rate, s.sor_rate, "
        "CASE WHEN s.sor_rate>0 THEN ROUND(i.unit_price_rate/s.sor_rate,1) END AS x_sor, i.total_cost "
        "FROM fact_po_item i LEFT JOIN fact_sor_item s "
        "ON s.tender_id=i.tender_id AND s.item_code=i.item_code "
        "WHERE i.tender_id=? ORDER BY i.serial_no", (tender_id,))
    flags = query("SELECT flag_id, score, rule_code, status, evidence FROM v_anomaly_worklist "
                  "WHERE tender_id=? ORDER BY score DESC", (tender_id,))
    docs = query("SELECT label, filename, file_size_bytes, mime_type FROM fact_document "
                 "WHERE tender_id=? LIMIT 200", (tender_id,))
    return render(request, "case.html", "/investigations",
                  t=t, pos=pos, items=items, flags=flags, docs=docs, saved=saved,
                  statuses=_STATUSES)


@app.post("/case/{tid}/review")
def review(tid: int, flag_id: int = Form(...), status: str = Form(...),
           note: str = Form(""), reviewer: str = Form("analyst")):
    data.update_flag(flag_id, status, note, reviewer)
    _totals.cache_clear()
    return RedirectResponse(f"/case/{tid}?saved=1", status_code=303)


# ============================================================ DATA EXPLORER
_SORTS = {"Newest": "t.publish_epoch DESC", "Highest estimate": "t.pac_amount DESC"}


def _explore_sql(text, dept, district, status, vendor, category, min_est, only_flagged):
    where, params = ["1=1"], []
    if text:
        where.append("(t.description LIKE ? OR t.ref_no LIKE ? OR CAST(t.org_tender_id AS TEXT) LIKE ?)")
        params += [f"%{text}%", f"%{text}%", f"%{text}%"]
    if dept:
        where.append("t.dept_id IN (SELECT department_id FROM dim_department WHERE name LIKE ?)")
        params.append(f"%{dept}%")
    if district == "__none__":
        where.append("NOT EXISTS (SELECT 1 FROM dim_department d "
                     "WHERE d.department_id=t.dept_id AND d.district IS NOT NULL)")
    elif district:
        where.append("t.dept_id IN (SELECT department_id FROM dim_department WHERE district = ?)")
        params.append(district)
    if status:
        where.append("t.listing_status = ?"); params.append(status)
    if category:
        where.append("pc.description LIKE ?"); params.append(f"%{category}%")
    if min_est and min_est > 0:
        where.append("t.pac_amount >= ?"); params.append(min_est * 1e7)
    if vendor:
        where.append("EXISTS (SELECT 1 FROM fact_purchase_order p JOIN dim_vendor v "
                     "ON v.vendor_id=p.vendor_id WHERE p.tender_id=t.tender_id AND v.name LIKE ?)")
        params.append(f"%{vendor}%")
    if only_flagged:
        where.append("EXISTS (SELECT 1 FROM fact_anomaly_flag f WHERE f.tender_id=t.tender_id)")
    return " AND ".join(where), params


def _explore_rows(text, dept, district, status, vendor, category, min_est, only_flagged, sort, limit):
    clause, params = _explore_sql(text, dept, district, status, vendor, category, min_est, only_flagged)
    order = _SORTS.get(sort, "t.publish_epoch DESC")
    sql = f"""
    WITH base AS (
      SELECT t.tender_id, t.org_tender_id, t.ref_no, t.description, t.dept_id, t.proc_cat_id,
             t.pac_amount, t.publish_date, t.publish_epoch, t.listing_status
      FROM fact_tender t
      LEFT JOIN dim_procurement_category pc ON pc.proc_cat_id=t.proc_cat_id
      WHERE {clause} ORDER BY {order} LIMIT ?)
    SELECT b.tender_id, b.org_tender_id AS org_id, b.ref_no, substr(b.description,1,80) AS description,
           d.name AS department, pc.description AS category, b.listing_status AS status,
           ROUND(COALESCE(b.pac_amount,0)/1e7,2) AS est_cr,
           substr(b.publish_date,1,10) AS published,
           ROUND((SELECT COALESCE(SUM(tr.trusted_po_value),0) FROM fact_purchase_order p
              JOIN v_po_value_trusted tr ON tr.po_id=p.po_id WHERE p.tender_id=b.tender_id)/1e7,2) AS award_cr,
           (SELECT v.name FROM fact_purchase_order p JOIN dim_vendor v ON v.vendor_id=p.vendor_id
              WHERE p.tender_id=b.tender_id LIMIT 1) AS vendor,
           (SELECT COUNT(*) FROM fact_anomaly_flag f WHERE f.tender_id=b.tender_id) AS flags
    FROM base b
    LEFT JOIN dim_department d ON d.department_id=b.dept_id
    LEFT JOIN dim_procurement_category pc ON pc.proc_cat_id=b.proc_cat_id
    """
    return query(sql, params + [limit])


@app.get("/explore", response_class=HTMLResponse)
def explore(request: Request, text: str = "", dept: str = "", district: str = "", status: str = "",
            vendor: str = "", category: str = "", min_est: float = 0.0, only_flagged: int = 0,
            sort: str = "Newest", limit: int = 300):
    rows = _explore_rows(text, dept, district, status, vendor, category, min_est,
                         bool(only_flagged), sort, limit)
    qs = {"text": text, "dept": dept, "district": district, "status": status, "vendor": vendor,
          "category": category, "min_est": min_est, "only_flagged": only_flagged,
          "sort": sort, "limit": limit}
    export_qs = "&".join(f"{k}={quote(str(v))}" for k, v in qs.items() if v not in ("", 0, 0.0))
    districts = [r["district"] for r in query(
        "SELECT DISTINCT district FROM dim_department WHERE district IS NOT NULL ORDER BY 1")]
    _order_pref = ["OPEN", "CLOSED", "UPCOMING", "CANCELLED", "CORRIGENDUM"]
    statuses = [r["listing_status"] for r in query(
        "SELECT DISTINCT listing_status FROM fact_tender WHERE listing_status IS NOT NULL")]
    statuses.sort(key=lambda s: _order_pref.index(s) if s in _order_pref else 99)
    return render(request, "explore.html", "/explore", rows=rows, f=qs, sorts=list(_SORTS),
                  districts=districts, statuses=statuses, export_qs=export_qs)


@app.get("/export.csv")
def export_csv(text: str = "", dept: str = "", district: str = "", status: str = "", vendor: str = "",
               category: str = "", min_est: float = 0.0, only_flagged: int = 0,
               sort: str = "Newest", limit: int = 2000):
    rows = _explore_rows(text, dept, district, status, vendor, category, min_est,
                         bool(only_flagged), sort, limit)
    buf = io.StringIO()
    cols = ["org_id", "tender_id", "ref_no", "description", "department", "category", "status",
            "est_cr", "award_cr", "vendor", "published", "flags"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=tenders.csv"})


# ============================================================ ENTITY LISTS
@app.get("/vendors", response_class=HTMLResponse)
def vendors(request: Request, q: str = ""):
    like = f"%{q}%"
    rows = query(
        "SELECT vendor_id AS id, name AS primary_name, total_value AS value, awards AS n1, "
        "n_departments AS n2 FROM v_vendor_concentration "
        + ("WHERE name LIKE ? " if q else "") +
        "ORDER BY total_value DESC LIMIT 200", ((like,) if q else ()))
    return render(request, "entity_list.html", "/vendors",
                  title="Vendors", kind="vendor", q=q, rows=rows,
                  cols=("Awards", "Departments"), placeholder="Search vendor name…")


@app.get("/departments", response_class=HTMLResponse)
def departments(request: Request, q: str = ""):
    like = f"%{q}%"
    rows = query(
        "SELECT dept_id AS id, dept_name AS primary_name, dept_value AS value, dept_awards AS n1, "
        "n_vendors AS n2 FROM v_dept_vendor_concentration "
        + ("WHERE dept_name LIKE ? " if q else "") +
        "ORDER BY dept_value DESC LIMIT 200", ((like,) if q else ()))
    return render(request, "entity_list.html", "/departments",
                  title="Departments", kind="department", q=q, rows=rows,
                  cols=("Awards", "Vendors"), placeholder="Search department name…")


@app.get("/officers", response_class=HTMLResponse)
def officers(request: Request, q: str = ""):
    like = f"%{q}%"
    rows = query(
        "SELECT oc.officer_id AS id, COALESCE(a.name,'Authority #'||oc.officer_id) AS primary_name, "
        "oc.total_value AS value, oc.n_awards AS n1, oc.n_vendors AS n2 "
        "FROM v_officer_concentration oc "
        "LEFT JOIN dim_authority a ON a.authority_id=oc.officer_id AND a.role='approving' "
        + ("WHERE a.name LIKE ? " if q else "") +
        "ORDER BY oc.total_value DESC LIMIT 200", ((like,) if q else ()))
    return render(request, "entity_list.html", "/officers",
                  title="Officers", kind="officer", q=q, rows=rows,
                  cols=("Awards", "Vendors"), placeholder="Search officer name…")


# ============================================================ ENTITY PROFILES
def _flags_rows(where_col, entity_id):
    return query(f"SELECT flag_id, score, rule_code, status, tender_id, vendor_name, dept_name, "
                 f"evidence FROM v_anomaly_worklist WHERE {where_col}=? ORDER BY score DESC LIMIT 100",
                 (entity_id,))


def _ego(nodes, links, center_name):
    cats = [{"name": "Focus"}, {"name": "Counterparty"}]
    return {"categories": cats, "nodes": nodes, "links": links}


@app.get("/vendor/{vendor_id}", response_class=HTMLResponse)
def vendor(request: Request, vendor_id: int):
    p = data.one("SELECT * FROM v_vendor_concentration WHERE vendor_id=?", (vendor_id,))
    if not p:
        return render(request, "notfound.html", "/vendors", what=f"vendor {vendor_id}")
    depts = query(
        "SELECT COALESCE(d.name,'dept '||vd.dept_id) AS name, vd.dept_id AS id, "
        "vd.total_value AS value, vd.n_awards FROM v_graph_vendor_dept vd "
        "LEFT JOIN dim_department d ON d.department_id=vd.dept_id "
        "WHERE vd.vendor_id=? ORDER BY vd.total_value DESC LIMIT 15", (vendor_id,))
    rp = query("SELECT name_1, name_2, shared_pan, shared_gstin FROM v_vendor_related_party "
               "WHERE vendor_id_1=? OR vendor_id_2=?", (vendor_id, vendor_id))
    flags = _flags_rows("vendor_id", vendor_id)
    kpis = [(num(p["awards"]), "Awards", False), (cr(p["total_value"]), "Trusted value", True),
            (num(p["n_departments"]), "Departments", False),
            (f"{100*(p['top_dept_share'] or 0):.0f}%", "Top-dept share", False),
            (p.get("pan") or "—", "PAN", False)]
    nodes = [{"id": f"v{vendor_id}", "name": p["name"] or f"Vendor {vendor_id}", "val": 40, "cat": 0,
              "color": "#f0553d"}]
    links = []
    for d in depts:
        nodes.append({"id": f"d{d['id']}", "name": d["name"], "cat": 1,
                      "val": 10 + (d["n_awards"] or 0)})
        links.append({"source": f"v{vendor_id}", "target": f"d{d['id']}"})
    return render(request, "entity.html", "/vendors",
                  title=p["name"] or f"Vendor {vendor_id}",
                  subtitle=f"{p.get('city') or ''} {p.get('state') or ''}".strip(),
                  kind="vendor", eid=vendor_id, kpis=kpis, bar_title="Departments supplied (trusted value)",
                  bars={"categories": [d["name"] for d in depts][::-1],
                        "values": [round((d["value"] or 0)/1e7, 2) for d in depts][::-1]},
                  flags=flags, related=rp, graph=_ego(nodes, links, p["name"]),
                  flag_link_col="tender_id")


@app.get("/department/{dept_id}", response_class=HTMLResponse)
def department(request: Request, dept_id: int):
    p = data.one("SELECT * FROM v_dept_vendor_concentration WHERE dept_id=?", (dept_id,))
    if not p:
        return render(request, "notfound.html", "/departments", what=f"department {dept_id}")
    captured = data.one("SELECT 1 FROM v_flag_vendor_capture WHERE dept_id=?", (dept_id,))
    vendors_ = query(
        "SELECT COALESCE(v.name,'vendor '||vd.vendor_id) AS name, vd.vendor_id AS id, "
        "vd.total_value AS value, vd.n_awards FROM v_graph_vendor_dept vd "
        "LEFT JOIN dim_vendor v ON v.vendor_id=vd.vendor_id "
        "WHERE vd.dept_id=? ORDER BY vd.total_value DESC LIMIT 15", (dept_id,))
    flags = _flags_rows("dept_id", dept_id)
    kpis = [(num(p["dept_awards"]), "Awards", False), (cr(p["dept_value"]), "Value", True),
            (num(p["n_vendors"]), "Vendors", False),
            (f"{100*(p['top_vendor_share'] or 0):.0f}%", "Top-vendor share", captured is not None),
            (f"{p['hhi']:.2f}" if p.get("hhi") is not None else "—", "HHI", False)]
    nodes = [{"id": f"d{dept_id}", "name": p["dept_name"] or f"Dept {dept_id}", "val": 40, "cat": 0,
              "color": "#f0553d"}]
    links = []
    for v in vendors_:
        nodes.append({"id": f"v{v['id']}", "name": v["name"], "cat": 1, "val": 10 + (v["n_awards"] or 0)})
        links.append({"source": f"d{dept_id}", "target": f"v{v['id']}"})
    return render(request, "entity.html", "/departments",
                  title=p["dept_name"] or f"Department {dept_id}", subtitle="",
                  warn=("One vendor dominates this department's awards (vendor capture)."
                        if captured else ""),
                  kind="department", eid=dept_id, kpis=kpis, bar_title="Vendors (by trusted value)",
                  bars={"categories": [v["name"] for v in vendors_][::-1],
                        "values": [round((v["value"] or 0)/1e7, 2) for v in vendors_][::-1]},
                  flags=flags, related=[], graph=_ego(nodes, links, p["dept_name"]),
                  flag_link_col="tender_id")


@app.get("/officer/{officer_id}", response_class=HTMLResponse)
def officer(request: Request, officer_id: int):
    p = data.one(
        "SELECT oc.*, a.name, a.designation, a.org_name FROM v_officer_concentration oc "
        "LEFT JOIN dim_authority a ON a.authority_id=oc.officer_id AND a.role='approving' "
        "WHERE oc.officer_id=?", (officer_id,))
    if not p:
        return render(request, "notfound.html", "/officers", what=f"officer {officer_id}")
    vs = query(
        "SELECT COALESCE(v.name,'vendor '||ov.vendor_id) AS name, ov.vendor_id AS id, "
        "ov.total_value AS value, ov.n_awards FROM v_officer_vendor ov "
        "LEFT JOIN dim_vendor v ON v.vendor_id=ov.vendor_id "
        "WHERE ov.officer_id=? ORDER BY ov.total_value DESC LIMIT 20", (officer_id,))
    flags = query(
        "SELECT w.flag_id, w.score, w.rule_code, w.status, w.tender_id, w.vendor_name, w.evidence "
        "FROM v_anomaly_worklist w JOIN fact_tender t ON t.tender_id=w.tender_id "
        "WHERE t.approving_authority_id=? ORDER BY w.score DESC LIMIT 100", (officer_id,))
    kpis = [(num(p["n_awards"]), "Awards approved", False), (cr(p["total_value"]), "Value", True),
            (num(p["n_vendors"]), "Vendors", False),
            (f"{100*(p['top_vendor_share'] or 0):.0f}%", "Top-vendor share", False)]
    nodes = [{"id": f"o{officer_id}", "name": p.get("name") or f"Authority #{officer_id}", "val": 40,
              "cat": 0, "color": "#9b7ede"}]
    links = []
    for v in vs:
        nodes.append({"id": f"v{v['id']}", "name": v["name"], "cat": 1, "val": 10 + (v["n_awards"] or 0)})
        links.append({"source": f"o{officer_id}", "target": f"v{v['id']}"})
    meta = " · ".join(x for x in [p.get("designation"), p.get("org_name")] if x)
    return render(request, "entity.html", "/officers",
                  title=p.get("name") or f"Authority #{officer_id}", subtitle=meta,
                  kind="officer", eid=officer_id, kpis=kpis, bar_title="Vendors approved (by value)",
                  bars={"categories": [v["name"] for v in vs][::-1],
                        "values": [round((v["value"] or 0)/1e7, 2) for v in vs][::-1]},
                  flags=flags, related=[], graph=_ego(nodes, links, p.get("name")),
                  flag_link_col="tender_id")


# ============================================================ MAP & TRENDS
def _district_rollup():
    from bihar_ingestion.geo import DISTRICTS, district_of
    depts = query("SELECT dept_id, dept_name, dept_value, dept_awards FROM v_dept_vendor_concentration")
    fl = {r["dept_id"]: r["n"] for r in query(
        "SELECT dept_id, COUNT(*) AS n FROM fact_anomaly_flag WHERE dept_id IS NOT NULL GROUP BY dept_id")}
    agg = {d: {"value": 0.0, "awards": 0, "flags": 0, "depts": 0} for d in DISTRICTS}
    unmapped = 0
    for r in depts:
        dist = district_of(r["dept_name"] or "")
        if dist is None or dist not in agg:
            unmapped += 1
            continue
        a = agg[dist]
        a["value"] += float(r["dept_value"] or 0)
        a["awards"] += int(r["dept_awards"] or 0)
        a["flags"] += int(fl.get(r["dept_id"], 0))
        a["depts"] += 1
    return agg, unmapped


@app.get("/map", response_class=HTMLResponse)
def map_page(request: Request, metric: str = "value"):
    agg, unmapped = _district_rollup()
    metric = metric if metric in ("value", "awards", "flags") else "value"
    def mval(a):
        return round(a["value"] / 1e7, 2) if metric == "value" else a[metric]
    choro = [{"name": d, "value": mval(a),
              "awards": a["awards"], "flags": a["flags"], "value_cr": round(a["value"]/1e7, 2)}
             for d, a in agg.items()]
    table = sorted(
        [{"district": d, "value_cr": round(a["value"]/1e7, 2), "awards": a["awards"],
          "flags": a["flags"], "depts": a["depts"]} for d, a in agg.items()],
        key=lambda r: r["value_cr" if metric == "value" else metric], reverse=True)
    tl = query(
        "SELECT substr(p.creation_date,1,7) AS ym, COUNT(*) AS n, "
        "SUM(tr.trusted_po_value) AS value FROM fact_purchase_order p "
        "JOIN v_po_value_trusted tr ON tr.po_id=p.po_id "
        "WHERE p.creation_date IS NOT NULL AND p.creation_date<>'' GROUP BY 1 ORDER BY 1")
    gj = json.loads(GEOJSON.read_text(encoding="utf-8")) if GEOJSON.exists() else None
    unit = {"value": "Cr", "awards": "awards", "flags": "flags"}[metric]
    return render(request, "map.html", "/map",
                  metric=metric, unmapped=unmapped, table=table, geojson=gj, unit=unit,
                  choro=choro,
                  timeline={"categories": [r["ym"] for r in tl],
                            "values": [round((r["value"] or 0)/1e7, 2) for r in tl],
                            "mark": [str(r["ym"]).endswith("-03") for r in tl]})


# ============================================================ NETWORK
@app.get("/network", response_class=HTMLResponse)
def network(request: Request, cluster: int = 0):
    import networkx as nx
    pairs = query("SELECT vendor_id_1, name_1, vendor_id_2, name_2, shared_pan, shared_gstin "
                  "FROM v_vendor_related_party")
    g = nx.Graph()
    names: dict[int, str] = {}
    for r in pairs:
        g.add_edge(int(r["vendor_id_1"]), int(r["vendor_id_2"]))
        names[int(r["vendor_id_1"])] = r["name_1"] or str(r["vendor_id_1"])
        names[int(r["vendor_id_2"])] = r["name_2"] or str(r["vendor_id_2"])
    clusters = sorted((sorted(c) for c in nx.connected_components(g)), key=len, reverse=True)
    rows = [{"cluster": i, "vendors": len(c), "names": " · ".join(names.get(v, str(v)) for v in c)}
            for i, c in enumerate(clusters, 1)]

    sel = cluster if 1 <= cluster <= len(clusters) else (1 if clusters else 0)
    detail, combined, shared_depts, gdata = [], 0.0, [], None
    if sel:
        members = clusters[sel - 1]
        ph = ",".join("?" * len(members))
        detail = query(f"SELECT vendor_id AS id, name, pan, total_value, awards, n_departments "
                       f"FROM v_vendor_concentration WHERE vendor_id IN ({ph})", members)
        combined = sum(float(d["total_value"] or 0) for d in detail)
        shared_depts = query(f"SELECT dept_id, COUNT(DISTINCT vendor_id) AS nv "
                             f"FROM v_graph_vendor_dept WHERE vendor_id IN ({ph}) "
                             f"GROUP BY dept_id HAVING nv>1", members)
        nodes = [{"id": str(v), "name": names.get(v, str(v)), "cat": 0,
                  "val": 16, "color": "#f0553d"} for v in members]
        links = [{"source": str(a), "target": str(b)}
                 for a, b in g.subgraph(members).edges()]
        gdata = {"categories": [{"name": "Related vendor"}], "nodes": nodes, "links": links}
    return render(request, "network.html", "/network",
                  n_pairs=len(pairs), n_clusters=len(clusters), rows=rows[:200],
                  sel=sel, detail=detail, combined=combined, shared_depts=len(shared_depts),
                  graph=gdata)


# ============================================================ CASEBOOK
_COOKIE_MAX = 60 * 60 * 24 * 365
_ITEM_KINDS = ("flag", "tender", "vendor", "department", "officer")
_KIND_HEADINGS = [("flag", "Flags"), ("tender", "Tenders"), ("vendor", "Vendors"),
                  ("department", "Departments"), ("officer", "Officers"), ("note", "Notes")]


def _enrich(items: list[dict]) -> list[dict]:
    """Attach a live detail line + href to each casebook item."""
    out = []
    for it in items:
        kind, ref = it["kind"], it["ref_id"]
        href, detail = None, (it.get("note") or "")
        if kind == "flag" and ref:
            r = data.one("SELECT score, rule_code, status, evidence, tender_id "
                         "FROM v_anomaly_worklist WHERE flag_id=?", (ref,))
            if r:
                detail = f"{r['rule_code']} · score {r['score']:.2f} · {r['evidence']}"
                href = f"/case/{_orgid(r['tender_id'])}" if r["tender_id"] else None
        elif kind == "tender" and ref:
            r = data.one("SELECT description, pac_amount, (SELECT name FROM dim_department d "
                         "WHERE d.department_id=t.dept_id) AS dept FROM fact_tender t "
                         "WHERE tender_id=?", (ref,))
            if r:
                detail = f"{(r['description'] or '')[:100]} — {r['dept'] or ''} · est {rupees(r['pac_amount'])}"
                href = f"/case/{_orgid(ref)}"
        elif kind == "vendor" and ref:
            r = data.one("SELECT name, total_value, awards, n_departments "
                         "FROM v_vendor_concentration WHERE vendor_id=?", (ref,))
            if r:
                detail = f"{r['name']} · {rupees(r['total_value'])} · {int(r['awards'])} awards · {int(r['n_departments'])} depts"
                href = f"/vendor/{ref}"
        elif kind == "department" and ref:
            r = data.one("SELECT dept_name, dept_value, dept_awards, top_vendor_share "
                         "FROM v_dept_vendor_concentration WHERE dept_id=?", (ref,))
            if r:
                detail = (f"{r['dept_name']} · {rupees(r['dept_value'])} · {int(r['dept_awards'])} "
                          f"awards · top vendor {100*(r['top_vendor_share'] or 0):.0f}%")
                href = f"/department/{ref}"
        elif kind == "officer" and ref:
            r = data.one("SELECT oc.total_value, oc.n_awards, a.name FROM v_officer_concentration oc "
                         "LEFT JOIN dim_authority a ON a.authority_id=oc.officer_id AND a.role='approving' "
                         "WHERE oc.officer_id=?", (ref,))
            if r:
                detail = f"{r['name'] or ('Authority #' + str(ref))} · {rupees(r['total_value'])} · {int(r['n_awards'])} approvals"
                href = f"/officer/{ref}"
        out.append({**it, "href": href, "detail": detail})
    return out


def _case_markdown(case: dict, items: list[dict]) -> str:
    from datetime import datetime
    L = [f"# {case['title']}", "",
         f"**Status:** {case['status']}  ",
         f"**Created:** {case['created_at']}  ",
         f"**Exported:** {datetime.now():%Y-%m-%d %H:%M}  ",
         f"**Items:** {len(items)}"]
    if case.get("summary"):
        L += ["", "## Summary", "", case["summary"]]
    for kind, heading in _KIND_HEADINGS:
        group = [i for i in items if i["kind"] == kind]
        if not group:
            continue
        L += ["", f"## {heading}", ""]
        for i in group:
            if kind == "note":
                L.append(f"- **{i['label']}** — {i['note']}")
            else:
                L.append(f"- **{i['label']}** — {i['detail']}")
                if i.get("note"):
                    L.append(f"    - _note:_ {i['note']}")
    L += ["", "---", "",
          "_Generated by the Bihar Procurement Integrity Monitor. "
          "Indicators are investigative leads, not verdicts._", ""]
    return "\n".join(L)


@app.get("/casebook", response_class=HTMLResponse)
def casebook_list(request: Request, need: int = 0):
    return render(request, "casebook_list.html", "/casebook",
                  cases=casebook.listing(), need=need)


@app.post("/casebook/new")
def casebook_new(title: str = Form(...), summary: str = Form("")):
    cid = casebook.create(title, summary)
    resp = RedirectResponse(f"/casebook/{cid}", status_code=303)
    resp.set_cookie("active_case", str(cid), max_age=_COOKIE_MAX, samesite="lax")
    return resp


@app.get("/casebook/{case_id}", response_class=HTMLResponse)
def casebook_detail(request: Request, case_id: int, added: int = 0):
    case = casebook.get(case_id)
    if not case:
        return render(request, "notfound.html", "/casebook", what=f"case {case_id}")
    case["items"] = _enrich(case["items"])
    return render(request, "casebook.html", "/casebook",
                  case=case, added=added, headings=_KIND_HEADINGS)


@app.post("/casebook/{case_id}/activate")
def casebook_activate(case_id: int, next: str = Form("")):
    dest = next if next.startswith("/") and not next.startswith("//") else f"/casebook/{case_id}"
    resp = RedirectResponse(dest, status_code=303)
    resp.set_cookie("active_case", str(case_id), max_age=_COOKIE_MAX, samesite="lax")
    return resp


@app.post("/casebook/{case_id}/meta")
def casebook_meta(case_id: int, title: str = Form(...), summary: str = Form("")):
    casebook.set_meta(case_id, title, summary)
    return RedirectResponse(f"/casebook/{case_id}", status_code=303)


@app.post("/casebook/{case_id}/status")
def casebook_status(case_id: int, status: str = Form(...)):
    casebook.set_status(case_id, status)
    return RedirectResponse(f"/casebook/{case_id}", status_code=303)


@app.post("/casebook/{case_id}/note")
def casebook_note(case_id: int, note: str = Form(...), label: str = Form("Note")):
    if note.strip():
        casebook.add_item(case_id, "note", None, label or "Note", note)
    return RedirectResponse(f"/casebook/{case_id}", status_code=303)


@app.post("/casebook/{case_id}/item/{item_id}/note")
def casebook_item_note(case_id: int, item_id: int, note: str = Form("")):
    casebook.update_note(case_id, item_id, note)
    return RedirectResponse(f"/casebook/{case_id}", status_code=303)


@app.post("/casebook/{case_id}/item/{item_id}/move")
def casebook_item_move(case_id: int, item_id: int, dir: str = Form("down")):
    casebook.move(case_id, item_id, "up" if dir == "up" else "down")
    return RedirectResponse(f"/casebook/{case_id}", status_code=303)


@app.post("/casebook/{case_id}/remove")
def casebook_remove(case_id: int, item_id: int = Form(...)):
    casebook.remove_item(case_id, item_id)
    return RedirectResponse(f"/casebook/{case_id}", status_code=303)


@app.post("/casebook/{case_id}/delete")
def casebook_delete(case_id: int):
    casebook.delete(case_id)
    return RedirectResponse("/casebook", status_code=303)


@app.post("/casebook/add")
async def casebook_add(request: Request):
    ac = request.cookies.get("active_case")
    if not (ac and ac.isdigit()) or not casebook.brief(int(ac)):
        return JSONResponse({"ok": False, "error": "no_active_case"}, status_code=400)
    form = await request.form()
    kind = form.get("kind"); ref = form.get("ref_id"); label = form.get("label") or ""
    if kind not in _ITEM_KINDS:
        return JSONResponse({"ok": False, "error": "bad_kind"}, status_code=400)
    _id, created = casebook.add_item(int(ac), kind, ref, str(label))
    b = casebook.brief(int(ac))
    return JSONResponse({"ok": True, "created": created, "count": b["n_items"],
                         "case_id": int(ac), "title": b["title"]})


@app.get("/casebook/{case_id}/export.md")
def casebook_export_md(case_id: int):
    case = casebook.get(case_id)
    if not case:
        return PlainTextResponse("not found", status_code=404)
    md = _case_markdown(case, _enrich(case["items"]))
    return StreamingResponse(iter([md]), media_type="text/markdown",
                             headers={"Content-Disposition": f"attachment; filename=case-{case_id}.md"})


@app.get("/casebook/{case_id}/export.json")
def casebook_export_json(case_id: int):
    case = casebook.get(case_id)
    if not case:
        return JSONResponse({"error": "not found"}, status_code=404)
    from datetime import datetime
    items = _enrich(case["items"])
    payload = {
        "title": case["title"], "summary": case["summary"], "status": case["status"],
        "created_at": case["created_at"], "exported_at": datetime.now().isoformat(timespec="seconds"),
        "n_items": len(items),
        "items": [{"kind": i["kind"], "label": i["label"], "ref_id": i["ref_id"],
                   "detail": i["detail"], "note": i["note"], "href": i["href"]} for i in items],
    }
    return JSONResponse(payload,
                        headers={"Content-Disposition": f"attachment; filename=case-{case_id}.json"})


@app.get("/casebook/{case_id}/print", response_class=HTMLResponse)
def casebook_print(request: Request, case_id: int):
    case = casebook.get(case_id)
    if not case:
        return render(request, "notfound.html", "/casebook", what=f"case {case_id}")
    case["items"] = _enrich(case["items"])
    from datetime import datetime
    return templates.TemplateResponse(request=request, name="casebook_print.html",
                                      context={"case": case, "headings": _KIND_HEADINGS,
                                               "now": datetime.now().strftime("%Y-%m-%d %H:%M")})
