-- Bihar eProcurement Ingestion Framework — analytics (gold) views
-- ================================================================
-- Read-optimized views over the canonical fact/dim tables for the analysis
-- engine. Views are cheap to (re)create and always reflect current data.

DROP VIEW IF EXISTS v_tender_full;
CREATE VIEW v_tender_full AS
SELECT
    t.tender_id,
    t.ref_no,
    t.description,
    t.listing_status,
    t.source_tab,
    t.status_code,
    ts.label                         AS status_label,
    t.pac_amount,
    t.tender_currency,
    t.dept_id,
    d.name                           AS department_name,
    d.code                           AS department_code,
    t.proc_cat_id,
    pc.description                   AS procurement_category,
    t.tender_type_id,
    rt.description                   AS tender_type,
    t.parent_tender_id,
    t.publish_date,
    t.bid_start_date,
    t.bid_end_date,
    t.bid_open_date,
    t.doc_submission_end_date,
    t.cancel_date,
    t.cancel_reason
FROM fact_tender t
LEFT JOIN dim_department            d  ON d.department_id = t.dept_id
LEFT JOIN dim_procurement_category  pc ON pc.proc_cat_id = t.proc_cat_id
LEFT JOIN dim_rfq_type              rt ON rt.rfq_type_id = t.tender_type_id
LEFT JOIN dim_status                ts ON ts.entity = 'tender' AND ts.status_code = t.status_code;

-- One row per awarded tender with contract + cycle-time metrics.
DROP VIEW IF EXISTS v_award_summary;
CREATE VIEW v_award_summary AS
SELECT
    t.tender_id,
    t.ref_no,
    t.description,
    t.dept_id,
    d.name                           AS department_name,
    pc.description                   AS procurement_category,
    t.pac_amount,
    po.po_id,
    po.po_value,
    po.currency,
    po.vendor_id,
    v.name                           AS vendor_name,
    t.publish_date,
    po.creation_date                 AS award_date,
    CASE
        WHEN t.publish_epoch IS NOT NULL AND po.creation_epoch IS NOT NULL
        THEN (po.creation_epoch - t.publish_epoch) / 86400000.0
    END                              AS award_cycle_days,
    CASE
        WHEN t.pac_amount IS NOT NULL AND t.pac_amount > 0 AND po.po_value IS NOT NULL
        THEN (po.po_value - t.pac_amount) / t.pac_amount * 100.0
    END                              AS award_vs_estimate_pct
FROM fact_tender t
JOIN fact_purchase_order            po ON po.tender_id = t.tender_id
LEFT JOIN dim_department            d  ON d.department_id = t.dept_id
LEFT JOIN dim_procurement_category  pc ON pc.proc_cat_id = t.proc_cat_id
LEFT JOIN dim_vendor                v  ON v.vendor_id = po.vendor_id;

-- Aggregated vendor performance.
DROP VIEW IF EXISTS v_vendor_profile;
CREATE VIEW v_vendor_profile AS
SELECT
    po.vendor_id,
    v.name                           AS vendor_name,
    v.vendor_code,
    v.gstin,
    v.uid_type,
    v.uid,
    v.city,
    v.state,
    COUNT(DISTINCT po.po_id)         AS award_count,
    COUNT(DISTINCT po.tender_id)     AS tender_count,
    SUM(po.po_value)                 AS total_awarded_value,
    AVG(po.po_value)                 AS avg_award_value,
    MIN(po.creation_date)            AS first_award_date,
    MAX(po.creation_date)            AS last_award_date,
    COUNT(DISTINCT t.dept_id)        AS distinct_departments
FROM fact_purchase_order po
LEFT JOIN dim_vendor v ON v.vendor_id = po.vendor_id
LEFT JOIN fact_tender t ON t.tender_id = po.tender_id
GROUP BY po.vendor_id, v.name, v.vendor_code, v.gstin, v.uid_type, v.uid, v.city, v.state;

-- Aggregated department activity.
DROP VIEW IF EXISTS v_department_profile;
CREATE VIEW v_department_profile AS
SELECT
    t.dept_id,
    d.name                           AS department_name,
    COUNT(*)                         AS tender_count,
    SUM(t.pac_amount)                AS total_estimated_value,
    COUNT(po.po_id)                  AS awarded_count,
    SUM(po.po_value)                 AS total_awarded_value,
    COUNT(CASE WHEN t.listing_status = 'CANCELLED' THEN 1 END) AS cancelled_count
FROM fact_tender t
LEFT JOIN dim_department      d  ON d.department_id = t.dept_id
LEFT JOIN fact_purchase_order po ON po.tender_id = t.tender_id
GROUP BY t.dept_id, d.name;

-- Full document inventory with tender context.
DROP VIEW IF EXISTS v_document_inventory;
CREATE VIEW v_document_inventory AS
SELECT
    doc.document_id,
    doc.tender_id,
    t.ref_no,
    doc.source,
    doc.label,
    doc.filename,
    doc.file_size_bytes,
    doc.download_url,
    doc.sha256,
    doc.downloaded_at
FROM fact_document doc
LEFT JOIN fact_tender t ON t.tender_id = doc.tender_id;

-- Corrigendum activity per tender.
DROP VIEW IF EXISTS v_corrigendum_summary;
CREATE VIEW v_corrigendum_summary AS
SELECT
    c.tender_id,
    t.ref_no,
    COUNT(*)              AS corrigendum_count,
    MAX(c.version_no)     AS latest_version,
    MAX(c.update_date)    AS last_corrigendum_date
FROM fact_corrigendum c
LEFT JOIN fact_tender t ON t.tender_id = c.tender_id
GROUP BY c.tender_id, t.ref_no;

-- Purchase-order line items joined to their PO, tender, and vendor.
DROP VIEW IF EXISTS v_po_line_items;
CREATE VIEW v_po_line_items AS
SELECT
    i.po_item_id,
    i.po_id,
    i.tender_id,
    t.ref_no,
    po.vendor_id,
    v.name                AS vendor_name,
    i.serial_no,
    i.item_code,
    i.item_name,
    i.uom,
    i.quantity,
    i.unit_price_rate,
    i.sub_total,
    i.total_tax,
    i.total_cost,
    i.remarks
FROM fact_po_item i
LEFT JOIN fact_purchase_order po ON po.po_id = i.po_id
LEFT JOIN fact_tender t ON t.tender_id = i.tender_id
LEFT JOIN dim_vendor v ON v.vendor_id = po.vendor_id;

