-- ============================================================
-- Value-reasonableness views  (Phase 2)
-- ------------------------------------------------------------
-- Two complementary "is this amount sensible?" detectors:
--   * v_flag_unit_cost_outlier  -- per-UNIT price vs category norm
--                                  (your streetlight-@-50L case). LIMITED:
--                                  only works on countable goods (qty>1),
--                                  because ~93% of line items are UOM 189
--                                  "Lump Sum" (whole works priced as a job,
--                                  qty=1) where no per-unit price exists.
--   * v_flag_award_vs_estimate  -- award vs tender estimate (pac_amount).
--                                  Broad: 94% of tenders have an estimate and
--                                  it is granularity-independent, so this is
--                                  the practical reasonableness signal here.
-- All money uses TRUSTED po value so Phase-1 corrupt cells don't leak in.
-- ============================================================

-- Inferred UOM classification (labels unknown for numeric codes; type inferred
-- from behaviour: 157 = percent [the GST lines]; 189/PMI/LumpSum/WORK = whole-
-- job lump sum with qty always 1). Anything else -> 'other' (the qty>1 gate in
-- the detector is what actually protects against lump-sum contamination).
DROP VIEW IF EXISTS dim_uom;
CREATE VIEW dim_uom AS
SELECT column1 AS uom_code, column2 AS uom_type FROM (VALUES
    ('157', 'percent'),
    ('189', 'lumpsum'), ('PMI', 'lumpsum'), ('LumpSum', 'lumpsum'), ('WORK', 'lumpsum')
);

-- Keyword categorisation of the (noisy) free-text item_name. The 'in light of'
-- / 'light of the' guards are from a real false positive (a Rs 116 Cr civil
-- work titled "In light of the announcement...").
DROP VIEW IF EXISTS v_item_category;
CREATE VIEW v_item_category AS
SELECT po_item_id, po_id, tender_id, item_name, quantity, unit_price_rate, uom,
    CASE
      WHEN (item_name LIKE '%street light%' OR item_name LIKE '%streetlight%'
            OR item_name LIKE '%led light%' OR item_name LIKE '%solar light%'
            OR (item_name LIKE '%light%'
                AND item_name NOT LIKE '%in light of%'
                AND item_name NOT LIKE '%light of the%')) THEN 'lighting'
      WHEN item_name LIKE '%transformer%'                              THEN 'transformer'
      WHEN item_name LIKE '%pump%' OR item_name LIKE '%motor%'         THEN 'pump'
      WHEN item_name LIKE '%pipe%'                                     THEN 'pipe'
      WHEN item_name LIKE '%tube well%' OR item_name LIKE '%tubewell%'
            OR item_name LIKE '%boring%'                               THEN 'tubewell'
      WHEN item_name LIKE '%computer%' OR item_name LIKE '%laptop%'
            OR item_name LIKE '%printer%' OR item_name LIKE '%desktop%' THEN 'it_equipment'
      WHEN item_name LIKE '%furniture%' OR item_name LIKE '%chair%'
            OR item_name LIKE '%desk%' OR item_name LIKE '%almirah%'    THEN 'furniture'
      WHEN item_name LIKE '%camera%' OR item_name LIKE '%cctv%'        THEN 'cctv'
      ELSE 'uncategorized'
    END AS category
FROM fact_po_item;

-- Per-category median unit price on the COUNTABLE subset (qty>1, real rate,
-- not lump-sum / percent). Median via window functions (SQLite has no MEDIAN).
DROP VIEW IF EXISTS v_unit_cost_median;
CREATE VIEW v_unit_cost_median AS
SELECT category, AVG(unit_cost) AS median_unit_cost, MAX(cnt) AS n
FROM (
    SELECT ic.category,
           i.unit_price_rate AS unit_cost,
           ROW_NUMBER() OVER (PARTITION BY ic.category ORDER BY i.unit_price_rate) AS rn,
           COUNT(*)     OVER (PARTITION BY ic.category)                            AS cnt
    FROM v_item_category ic
    JOIN fact_po_item i    ON i.po_item_id = ic.po_item_id
    LEFT JOIN dim_uom u    ON u.uom_code = i.uom
    WHERE i.quantity > 1 AND i.unit_price_rate > 0
      AND ic.category <> 'uncategorized'
      AND COALESCE(u.uom_type, 'other') NOT IN ('lumpsum', 'percent')
)
WHERE rn IN ((cnt + 1) / 2, (cnt + 2) / 2)     -- the middle 1-2 rows
GROUP BY category;

-- FLAG: per-unit price far above its category norm (streetlight @ 50L). Only
-- categories with >= 5 countable observations are benchmarked.
DROP VIEW IF EXISTS v_flag_unit_cost_outlier;
CREATE VIEW v_flag_unit_cost_outlier AS
WITH goods AS (
    SELECT ic.category, i.po_id, i.tender_id, i.po_item_id,
           i.item_name, i.quantity, i.unit_price_rate AS unit_cost
    FROM v_item_category ic
    JOIN fact_po_item i    ON i.po_item_id = ic.po_item_id
    LEFT JOIN dim_uom u    ON u.uom_code = i.uom
    WHERE i.quantity > 1 AND i.unit_price_rate > 0
      AND ic.category <> 'uncategorized'
      AND COALESCE(u.uom_type, 'other') NOT IN ('lumpsum', 'percent')
)
SELECT g.category, g.po_id, g.tender_id, g.item_name, g.quantity,
       g.unit_cost, m.median_unit_cost,
       g.unit_cost / NULLIF(m.median_unit_cost, 0) AS x_median,
       m.n AS category_n
FROM goods g
JOIN v_unit_cost_median m ON m.category = g.category
WHERE m.n >= 5
  AND g.unit_cost > 5 * m.median_unit_cost;

-- FLAG: award vs tender estimate. Both directions matter -- 'overrun' (>=3x,
-- possible over-billing) and 'lowball' (<=0.4x, win-cheap-then-inflate). Uses
-- trusted value; ignores trivial estimates (< Rs 1 lakh) to cut noise.
DROP VIEW IF EXISTS v_flag_award_vs_estimate;
CREATE VIEW v_flag_award_vs_estimate AS
SELECT p.po_id, p.tender_id, t.dept_id,
       tr.trusted_po_value, t.pac_amount AS estimate,
       tr.trusted_po_value / t.pac_amount AS ratio,
       CASE WHEN tr.trusted_po_value / t.pac_amount >= 3   THEN 'overrun'
            WHEN tr.trusted_po_value / t.pac_amount <= 0.4 THEN 'lowball' END AS direction,
       tr.data_quality_flag
FROM fact_purchase_order p
JOIN fact_tender t          ON t.tender_id = p.tender_id
JOIN v_po_value_trusted tr  ON tr.po_id = p.po_id
WHERE t.pac_amount >= 100000
  AND tr.trusted_po_value > 0
  AND ( tr.trusted_po_value / t.pac_amount >= 3
     OR tr.trusted_po_value / t.pac_amount <= 0.4 );

-- FLAG: awarded unit rate far above the tender's OWN Schedule-of-Rates (SOR)
-- estimate for the SAME item (join on tender_id + item_code). Works even for
-- lump-sum works -- it compares like-for-like (awarded rate vs estimated rate),
-- so it sidesteps the per-unit problem.
-- Rigor guards: sor_rate > 1 excludes the "Re 1" placeholder estimates (which
-- otherwise yield meaningless million-x ratios); those cases are covered by the
-- award-vs-estimate detector via pac_amount instead.
DROP VIEW IF EXISTS v_flag_sor_overprice;
CREATE VIEW v_flag_sor_overprice AS
SELECT poi.tender_id, poi.po_id, poi.item_code,
       substr(poi.item_name, 1, 40) AS item_name,
       poi.quantity,
       poi.unit_price_rate       AS awarded_rate,
       s.sor_rate,
       poi.unit_price_rate / s.sor_rate AS rate_ratio
FROM fact_po_item poi
JOIN fact_sor_item s
  ON s.tender_id = poi.tender_id AND s.item_code = poi.item_code
WHERE s.sor_rate > 1
  AND poi.unit_price_rate > 0
  AND poi.unit_price_rate / s.sor_rate >= 2;

-- FLAG: possible tender-splitting / threshold-structuring. A department awarding
-- MANY sub-Rs-1Cr contracts to the SAME vendor within one month (that together
-- exceed Rs 50 L) can indicate one large job split to stay under an approval
-- tier. Heuristic (no official Rs thresholds available) -> disposition=review.
DROP VIEW IF EXISTS v_flag_tender_splitting;
CREATE VIEW v_flag_tender_splitting AS
WITH awards AS (
    SELECT t.dept_id, p.vendor_id, tr.trusted_po_value AS val,
           substr(p.creation_date, 1, 7) AS ym
    FROM fact_purchase_order p
    JOIN fact_tender t         ON t.tender_id = p.tender_id
    JOIN v_po_value_trusted tr ON tr.po_id = p.po_id
    WHERE p.vendor_id IS NOT NULL AND t.dept_id IS NOT NULL
      AND p.creation_date IS NOT NULL AND p.creation_date <> ''
      AND tr.trusted_po_value > 0
)
SELECT dept_id, vendor_id, ym,
       COUNT(*)  AS n_awards,
       SUM(val)  AS total_value,
       MAX(val)  AS max_award
FROM awards
GROUP BY dept_id, vendor_id, ym
HAVING COUNT(*) >= 4              -- >= 4 awards to one vendor in one month
   AND MAX(val) < 10000000        -- each under Rs 1 Cr (could have been one tender)
   AND SUM(val) > 5000000;        -- but together material (> Rs 50 L)

-- Category median award value (window-function median; SQLite has no MEDIAN).
DROP VIEW IF EXISTS v_category_value_median;
CREATE VIEW v_category_value_median AS
SELECT proc_cat_id, AVG(val) AS median_val, MAX(cnt) AS n
FROM (
    SELECT t.proc_cat_id, tr.trusted_po_value AS val,
           ROW_NUMBER() OVER (PARTITION BY t.proc_cat_id ORDER BY tr.trusted_po_value) AS rn,
           COUNT(*)     OVER (PARTITION BY t.proc_cat_id)                             AS cnt
    FROM fact_purchase_order p
    JOIN fact_tender t         ON t.tender_id = p.tender_id
    JOIN v_po_value_trusted tr ON tr.po_id = p.po_id
    WHERE tr.trusted_po_value > 0 AND t.proc_cat_id IS NOT NULL
)
WHERE rn IN ((cnt + 1) / 2, (cnt + 2) / 2)
GROUP BY proc_cat_id;

-- FLAG: statistical value outlier -- an award in the TOP 1% of its procurement
-- category AND >= 10x the category median. Category-normalised so a large Goods
-- award is judged against Goods, not against civil works. Uses trusted value.
DROP VIEW IF EXISTS v_flag_value_outlier;
CREATE VIEW v_flag_value_outlier AS
WITH ranked AS (
    SELECT p.po_id, p.tender_id, p.vendor_id, t.dept_id, t.proc_cat_id,
           tr.trusted_po_value AS val,
           PERCENT_RANK() OVER (PARTITION BY t.proc_cat_id ORDER BY tr.trusted_po_value) AS pr,
           COUNT(*)       OVER (PARTITION BY t.proc_cat_id)                              AS cat_n
    FROM fact_purchase_order p
    JOIN fact_tender t         ON t.tender_id = p.tender_id
    JOIN v_po_value_trusted tr ON tr.po_id = p.po_id
    WHERE tr.trusted_po_value > 0 AND t.proc_cat_id IS NOT NULL
)
SELECT r.po_id, r.tender_id, r.vendor_id, r.dept_id, r.proc_cat_id, r.val,
       r.pr, r.cat_n, m.median_val,
       r.val / NULLIF(m.median_val, 0) AS x_median
FROM ranked r
JOIN v_category_value_median m ON m.proc_cat_id = r.proc_cat_id
WHERE r.cat_n >= 100 AND r.pr >= 0.99 AND r.val >= 10 * m.median_val;
