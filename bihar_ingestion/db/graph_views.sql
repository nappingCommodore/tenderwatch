-- ============================================================
-- Relationship-graph projection  (property graph over the FKs)
-- ------------------------------------------------------------
-- The procurement data is already a graph hiding inside foreign keys. These
-- views expose it explicitly so capture / related-party / structuring patterns
-- become tractable (and exportable to NetworkX / Gephi / Neo4j).
--
-- * Node ids are TYPE-PREFIXED (V: D: T: PO: C: O:) => globally unique.
-- * Edge weights use TRUSTED po value (v_po_value_trusted) so the corrupt
--   cells caught in Phase 1 never distort centrality / concentration metrics.
-- ============================================================

-- ---- NODES -------------------------------------------------
DROP VIEW IF EXISTS v_graph_nodes;
CREATE VIEW v_graph_nodes AS
    SELECT 'V:'  || vendor_id AS node_id, 'vendor' AS node_type,
           COALESCE(name, '(unnamed vendor)') AS label,
           COALESCE(uid, gstin) AS detail, NULL AS value_num
    FROM dim_vendor
UNION ALL
    SELECT 'D:'  || department_id, 'department', COALESCE(name, '(unnamed dept)'),
           code, NULL
    FROM dim_department
UNION ALL
    SELECT 'C:'  || proc_cat_id, 'category', description, NULL, NULL
    FROM dim_procurement_category
UNION ALL
    SELECT 'O:'  || approving_authority_id, 'officer',
           'authority ' || approving_authority_id, 'approver', NULL
    FROM (SELECT DISTINCT approving_authority_id FROM fact_tender
          WHERE approving_authority_id IS NOT NULL)
UNION ALL
    SELECT 'T:'  || tender_id, 'tender',
           COALESCE(NULLIF(substr(description, 1, 60), ''), ref_no, 'tender ' || tender_id),
           ref_no, pac_amount
    FROM fact_tender
UNION ALL
    SELECT 'PO:' || po_id, 'po', 'PO ' || po_id, po_ref, po_value
    FROM fact_purchase_order;

-- ---- EDGES (granular award chain + hierarchy + approver) ----
DROP VIEW IF EXISTS v_graph_edges;
CREATE VIEW v_graph_edges AS
    SELECT 'V:'  || p.vendor_id AS src, 'PO:' || p.po_id AS dst,
           'AWARDED' AS edge_type, tr.trusted_po_value AS weight_value, 1 AS weight_count
    FROM fact_purchase_order p
    JOIN v_po_value_trusted tr ON tr.po_id = p.po_id
    WHERE p.vendor_id IS NOT NULL
UNION ALL
    SELECT 'PO:' || po_id, 'T:' || tender_id, 'FOR', NULL, 1
    FROM fact_purchase_order
UNION ALL
    SELECT 'T:' || tender_id, 'D:' || dept_id, 'ISSUED_BY', pac_amount, 1
    FROM fact_tender WHERE dept_id IS NOT NULL
UNION ALL
    SELECT 'T:' || tender_id, 'C:' || proc_cat_id, 'IN_CATEGORY', NULL, 1
    FROM fact_tender WHERE proc_cat_id IS NOT NULL
UNION ALL
    SELECT 'T:' || tender_id, 'O:' || approving_authority_id, 'APPROVED_BY', NULL, 1
    FROM fact_tender WHERE approving_authority_id IS NOT NULL
UNION ALL
    SELECT 'D:' || department_id, 'D:' || parent_id, 'CHILD_OF', NULL, 1
    FROM dim_department WHERE parent_id IS NOT NULL;

-- ============================================================
-- AGGREGATED PROJECTIONS + ANOMALY METRICS
-- ============================================================

-- Vendor <-> Department "SUPPLIES" edge: the workhorse for concentration.
DROP VIEW IF EXISTS v_graph_vendor_dept;
CREATE VIEW v_graph_vendor_dept AS
SELECT p.vendor_id, t.dept_id,
       COUNT(*)                    AS n_awards,
       SUM(tr.trusted_po_value)    AS total_value
FROM fact_purchase_order p
JOIN fact_tender t         ON t.tender_id = p.tender_id
JOIN v_po_value_trusted tr ON tr.po_id = p.po_id
WHERE p.vendor_id IS NOT NULL AND t.dept_id IS NOT NULL
GROUP BY p.vendor_id, t.dept_id;

-- Per-department vendor concentration (Herfindahl index + top-vendor share).
-- HHI in [0,1]; 1 = single-vendor monopoly. High HHI + high top share on a
-- department with enough spend = "vendor capture" red flag.
DROP VIEW IF EXISTS v_dept_vendor_concentration;
CREATE VIEW v_dept_vendor_concentration AS
WITH dept_tot AS (
    SELECT dept_id, SUM(total_value) AS dept_value, SUM(n_awards) AS dept_awards,
           COUNT(*) AS n_vendors
    FROM v_graph_vendor_dept GROUP BY dept_id
)
SELECT vd.dept_id, dp.name AS dept_name,
       dt.dept_value, dt.dept_awards, dt.n_vendors,
       MAX(vd.total_value)                                 AS top_vendor_value,
       MAX(vd.total_value) / NULLIF(dt.dept_value, 0)      AS top_vendor_share,
       SUM( (vd.total_value / NULLIF(dt.dept_value, 0))
          * (vd.total_value / NULLIF(dt.dept_value, 0)) )  AS hhi
FROM v_graph_vendor_dept vd
JOIN dept_tot dt ON dt.dept_id = vd.dept_id
LEFT JOIN dim_department dp ON dp.department_id = vd.dept_id
GROUP BY vd.dept_id;

-- Flagged capture: departments with real spend where one vendor dominates.
DROP VIEW IF EXISTS v_flag_vendor_capture;
CREATE VIEW v_flag_vendor_capture AS
SELECT dept_id, dept_name, dept_value, dept_awards, n_vendors,
       top_vendor_value, top_vendor_share, hhi
FROM v_dept_vendor_concentration
WHERE dept_awards >= 5
  AND top_vendor_share >= 0.60;

-- Per-vendor rollup: reach across departments + single-department dominance.
DROP VIEW IF EXISTS v_vendor_concentration;
CREATE VIEW v_vendor_concentration AS
SELECT vd.vendor_id, v.name, v.uid AS pan, v.city, v.state,
       SUM(vd.n_awards)                                AS awards,
       SUM(vd.total_value)                             AS total_value,
       COUNT(*)                                        AS n_departments,
       MAX(vd.total_value)                             AS top_dept_value,
       MAX(vd.total_value) / NULLIF(SUM(vd.total_value), 0) AS top_dept_share
FROM v_graph_vendor_dept vd
LEFT JOIN dim_vendor v ON v.vendor_id = vd.vendor_id
GROUP BY vd.vendor_id;

-- Related-party: DISTINCT vendor records that share a PAN (uid) or GSTIN.
-- Could be a benign dedup gap OR deliberate identity-splitting to dodge
-- concentration limits -- either way, flag with evidence (never merge blindly).
DROP VIEW IF EXISTS v_vendor_related_party;
CREATE VIEW v_vendor_related_party AS
SELECT a.vendor_id AS vendor_id_1, a.name AS name_1,
       b.vendor_id AS vendor_id_2, b.name AS name_2,
       CASE WHEN a.uid   IS NOT NULL AND a.uid   = b.uid   THEN 'PAN:'   || a.uid   END AS shared_pan,
       CASE WHEN a.gstin IS NOT NULL AND a.gstin = b.gstin THEN 'GSTIN:' || a.gstin END AS shared_gstin
FROM dim_vendor a
JOIN dim_vendor b ON a.vendor_id < b.vendor_id
WHERE ( a.uid   IS NOT NULL AND TRIM(a.uid)   <> '' AND a.uid   = b.uid )
   OR ( a.gstin IS NOT NULL AND TRIM(a.gstin) <> '' AND a.gstin = b.gstin );

-- Officer (approving authority) <-> vendor award aggregation (trusted value).
DROP VIEW IF EXISTS v_officer_vendor;
CREATE VIEW v_officer_vendor AS
SELECT t.approving_authority_id AS officer_id, p.vendor_id,
       COUNT(*)                 AS n_awards,
       SUM(tr.trusted_po_value) AS total_value
FROM fact_purchase_order p
JOIN fact_tender t         ON t.tender_id = p.tender_id
JOIN v_po_value_trusted tr ON tr.po_id = p.po_id
WHERE t.approving_authority_id IS NOT NULL AND p.vendor_id IS NOT NULL
GROUP BY t.approving_authority_id, p.vendor_id;

-- Per-officer concentration: how much of an officer's awarded value goes to
-- their single top vendor (repeat-approver / nexus signal).
DROP VIEW IF EXISTS v_officer_concentration;
CREATE VIEW v_officer_concentration AS
WITH tot AS (
    SELECT officer_id, SUM(total_value) AS total_value, SUM(n_awards) AS n_awards,
           COUNT(*) AS n_vendors
    FROM v_officer_vendor GROUP BY officer_id
)
SELECT ov.officer_id, tot.total_value, tot.n_awards, tot.n_vendors,
       MAX(ov.total_value)                            AS top_vendor_value,
       MAX(ov.total_value) / NULLIF(tot.total_value, 0) AS top_vendor_share
FROM v_officer_vendor ov JOIN tot ON tot.officer_id = ov.officer_id
GROUP BY ov.officer_id;

-- Vendor <-> District aggregation (department -> its Bihar district).
DROP VIEW IF EXISTS v_graph_vendor_district;
CREATE VIEW v_graph_vendor_district AS
SELECT p.vendor_id, d.district,
       COUNT(*)                 AS n_awards,
       SUM(tr.trusted_po_value) AS total_value
FROM fact_purchase_order p
JOIN fact_tender t         ON t.tender_id = p.tender_id
JOIN dim_department d      ON d.department_id = t.dept_id
JOIN v_po_value_trusted tr ON tr.po_id = p.po_id
WHERE p.vendor_id IS NOT NULL AND d.district IS NOT NULL
GROUP BY p.vendor_id, d.district;

-- Per-district vendor concentration (HHI + top-vendor share). Districts pool
-- many departments, so a single vendor holding a large share is significant.
DROP VIEW IF EXISTS v_district_vendor_concentration;
CREATE VIEW v_district_vendor_concentration AS
WITH tot AS (
    SELECT district, SUM(total_value) AS dist_value, SUM(n_awards) AS dist_awards,
           COUNT(*) AS n_vendors
    FROM v_graph_vendor_district GROUP BY district
)
SELECT vd.district, tot.dist_value, tot.dist_awards, tot.n_vendors,
       MAX(vd.total_value)                              AS top_vendor_value,
       MAX(vd.total_value) / NULLIF(tot.dist_value, 0)  AS top_vendor_share,
       SUM( (vd.total_value / NULLIF(tot.dist_value, 0))
          * (vd.total_value / NULLIF(tot.dist_value, 0)) ) AS hhi
FROM v_graph_vendor_district vd
JOIN tot ON tot.district = vd.district
GROUP BY vd.district;

-- Flag: the top vendor holds a notable share of a whole district's spend.
-- NOTE: districts pool many departments, so shares dilute (max ~24% here);
-- this surfaces the biggest district players, not outright "dominance"
-- (dominance is a department-level phenomenon -> see v_flag_vendor_capture).
DROP VIEW IF EXISTS v_flag_vendor_district_capture;
CREATE VIEW v_flag_vendor_district_capture AS
SELECT district, dist_value, dist_awards, n_vendors,
       top_vendor_value, top_vendor_share, hhi
FROM v_district_vendor_concentration
WHERE dist_awards >= 20 AND top_vendor_share >= 0.15;
