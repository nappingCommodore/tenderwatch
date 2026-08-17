-- ============================================================
-- Anomaly / data-quality views  (Phase 1: DATA-CAPTURE ARTIFACTS)
-- ------------------------------------------------------------
-- Additive & READ-ONLY. These views NEVER mutate source data; the raw
-- value is always preserved in fact_* / raw_response. Each view emits the
-- exact evidence that fired so a flagged number can be *adjudicated*, never
-- silently discarded (project tenet: misclassifying real corruption as
-- "junk" is the cardinal sin).
--
-- Scope of Phase 1 = artifacts only: values that are internally
-- impossible / malformed at capture time. Genuine procurement anomalies
-- (over-pricing, favouritism, structuring) are later phases.
-- ============================================================

-- A1 -- DIGIT DOUBLING / CONCATENATION -----------------------
-- A value whose digit string is one plausible number written twice, e.g.
-- poValue = 19689251968925 = "1968925" || "1968925"  (true value ~19.7 lakh).
DROP VIEW IF EXISTS v_artifact_digit_doubled;
CREATE VIEW v_artifact_digit_doubled AS
WITH base AS (
    SELECT po_id, tender_id, po_value,
           CAST(CAST(po_value AS INTEGER) AS TEXT) AS s
    FROM fact_purchase_order
    WHERE po_value IS NOT NULL AND po_value > 0
)
SELECT po_id, tender_id,
       po_value                                        AS raw_value,
       CAST(substr(s, 1, LENGTH(s) / 2) AS INTEGER)    AS repaired_value,
       LENGTH(s)                                       AS n_digits,
       'A1_digit_doubled'                              AS rule_code
FROM base
WHERE LENGTH(s) >= 10                       -- ignore short coincidences
  AND LENGTH(s) % 2 = 0
  AND substr(s, 1, LENGTH(s) / 2) = substr(s, LENGTH(s) / 2 + 1, LENGTH(s) / 2);

-- A2 -- CORRUPT PERCENTAGE / TAX LINE ------------------------
-- A line-item labelled as a %/GST adjustment whose own total EXCEEDS the sum
-- of every other line in the PO. A percentage of a base can never exceed the
-- base, so the cell is provably corrupt (e.g. tube-well "Additional 0.0633%
-- GST" = Rs 34,506 Cr vs Rs 8 Cr of real work).
-- Rigor guards: (a) match only unambiguous %-lines -- the token "gst" or a
-- digit immediately followed by "%" -- NOT bare "tax"/"cess" (would hit
-- "tax office", "success"); (b) require base_value > 0 so the "exceeds base"
-- proof actually holds (a lone line has no base to compare against).
DROP VIEW IF EXISTS v_artifact_pct_tax_line;
CREATE VIEW v_artifact_pct_tax_line AS
WITH tax_lines AS (
    SELECT po_item_id, po_id, tender_id, serial_no, item_name,
           COALESCE(total_cost, 0) AS total_cost
    FROM fact_po_item
    WHERE COALESCE(total_cost, 0) > 0
      AND ( item_name LIKE '%gst%'
         OR item_name GLOB '*[0-9]%*' )       -- a digit immediately before '%'
),
scored AS (
    SELECT t.*,
           COALESCE((SELECT SUM(COALESCE(o.total_cost, 0)) FROM fact_po_item o
                     WHERE o.po_id = t.po_id AND o.po_item_id <> t.po_item_id), 0) AS base_value
    FROM tax_lines t
)
SELECT po_id, tender_id, po_item_id, serial_no, item_name,
       total_cost AS tax_line_value, base_value, 'A2_pct_tax_line' AS rule_code
FROM scored
WHERE base_value > 0
  AND total_cost > base_value;

-- A3 -- SCALE / PAISE ERROR vs ESTIMATE ----------------------
-- Award that is ~100 / 1,000 / 10,000 / 100,000 x the tender estimate is far
-- more likely a decimal-shift (value stored in paise / with extra zeros) than
-- a real 10,000%+ overrun.
DROP VIEW IF EXISTS v_artifact_scale_error;
CREATE VIEW v_artifact_scale_error AS
SELECT p.po_id, p.tender_id,
       p.po_value                     AS raw_value,
       t.pac_amount                   AS estimate,
       p.po_value / t.pac_amount      AS ratio,
       CASE
         WHEN p.po_value / t.pac_amount BETWEEN 95     AND 105     THEN p.po_value / 100.0
         WHEN p.po_value / t.pac_amount BETWEEN 950    AND 1050    THEN p.po_value / 1000.0
         WHEN p.po_value / t.pac_amount BETWEEN 9500   AND 10500   THEN p.po_value / 10000.0
         WHEN p.po_value / t.pac_amount BETWEEN 95000  AND 105000  THEN p.po_value / 100000.0
       END                            AS repaired_value,
       'A3_scale_error'               AS rule_code
FROM fact_purchase_order p
JOIN fact_tender t ON t.tender_id = p.tender_id
WHERE t.pac_amount > 0 AND p.po_value > 0
  AND ( p.po_value / t.pac_amount BETWEEN 95     AND 105
     OR p.po_value / t.pac_amount BETWEEN 950    AND 1050
     OR p.po_value / t.pac_amount BETWEEN 9500   AND 10500
     OR p.po_value / t.pac_amount BETWEEN 95000  AND 105000 );

-- A4 -- REPEATED-DIGIT SENTINEL ------------------------------
-- A value made of one repeated digit (999999, 1111111 ...) is a classic
-- "not really entered" placeholder.
DROP VIEW IF EXISTS v_artifact_repeated_digit;
CREATE VIEW v_artifact_repeated_digit AS
WITH base AS (
    SELECT po_id, tender_id, po_value,
           CAST(CAST(po_value AS INTEGER) AS TEXT) AS s
    FROM fact_purchase_order
    WHERE po_value IS NOT NULL AND po_value > 0
)
SELECT po_id, tender_id, po_value AS raw_value, s AS digits,
       'A4_repeated_digit' AS rule_code
FROM base
WHERE LENGTH(s) >= 6
  AND LENGTH(REPLACE(s, substr(s, 1, 1), '')) = 0;

-- A5 -- EPOCH / DATE PASTED AS AMOUNT ------------------------
-- A value sitting in the millisecond-epoch band (~2014-2028) is very likely a
-- timestamp mis-mapped into the amount field.
DROP VIEW IF EXISTS v_artifact_epoch_value;
CREATE VIEW v_artifact_epoch_value AS
SELECT po_id, tender_id, po_value AS raw_value, 'A5_epoch_as_value' AS rule_code
FROM fact_purchase_order
WHERE po_value BETWEEN 1400000000000 AND 1850000000000
  AND po_value = CAST(po_value AS INTEGER);

-- ============================================================
-- ROLL-UPS
-- ============================================================

-- Master ranked list of flagged "red numbers" (Phase-1 artifacts only).
-- perspective = artifact | signal ; disposition = quarantine | review | ok.
DROP VIEW IF EXISTS v_red_numbers;
CREATE VIEW v_red_numbers AS
    SELECT 'po'      AS entity_type, po_id      AS entity_id, tender_id, rule_code,
           'artifact' AS perspective, 'quarantine' AS disposition,
           raw_value, repaired_value,
           'digits=' || n_digits || ', both halves equal -> number written twice' AS evidence
    FROM v_artifact_digit_doubled
UNION ALL
    SELECT 'po_item', po_item_id, tender_id, rule_code,
           'artifact', 'quarantine',
           tax_line_value, base_value,
           'pct/tax line ' || printf('%.0f', tax_line_value)
             || ' exceeds base ' || printf('%.0f', base_value) || ' (impossible for a %)' AS evidence
    FROM v_artifact_pct_tax_line
UNION ALL
    SELECT 'po', po_id, tender_id, rule_code,
           'artifact', 'quarantine',
           raw_value, NULL,
           'all identical digits: ' || digits
    FROM v_artifact_repeated_digit
UNION ALL
    SELECT 'po', po_id, tender_id, rule_code,
           'artifact', 'review',
           raw_value, repaired_value,
           'po_value ~= ' || printf('%.0f', ratio) || 'x estimate (scale/paise error?)'
    FROM v_artifact_scale_error
UNION ALL
    SELECT 'po', po_id, tender_id, rule_code,
           'artifact', 'review',
           raw_value, NULL,
           'value in ms-epoch band (date pasted as amount?)'
    FROM v_artifact_epoch_value;

-- Per-PO trusted value: raw preserved, repaired supplied where a repair is
-- unambiguous. data_quality_flag = NULL means "clean / no artifact detected".
DROP VIEW IF EXISTS v_po_value_trusted;
CREATE VIEW v_po_value_trusted AS
SELECT p.po_id, p.tender_id, p.vendor_id,
       p.po_value AS raw_po_value,
       COALESCE(dd.repaired_value, sc.repaired_value, pt.base_value, p.po_value) AS trusted_po_value,
       CASE
         WHEN dd.po_id IS NOT NULL THEN 'A1_digit_doubled'
         WHEN pt.po_id IS NOT NULL THEN 'A2_pct_tax_line'
         WHEN sc.po_id IS NOT NULL THEN 'A3_scale_error'
         WHEN rd.po_id IS NOT NULL THEN 'A4_repeated_digit'
         WHEN ev.po_id IS NOT NULL THEN 'A5_epoch_as_value'
         ELSE NULL
       END AS data_quality_flag
FROM fact_purchase_order p
LEFT JOIN v_artifact_digit_doubled  dd ON dd.po_id = p.po_id
LEFT JOIN v_artifact_scale_error    sc ON sc.po_id = p.po_id
LEFT JOIN v_artifact_repeated_digit rd ON rd.po_id = p.po_id
LEFT JOIN v_artifact_epoch_value    ev ON ev.po_id = p.po_id
LEFT JOIN (SELECT po_id, MIN(base_value) AS base_value
           FROM v_artifact_pct_tax_line GROUP BY po_id) pt ON pt.po_id = p.po_id;

-- Quick summary: how many flags per rule / disposition.
DROP VIEW IF EXISTS v_data_quality_summary;
CREATE VIEW v_data_quality_summary AS
SELECT rule_code, perspective, disposition,
       COUNT(*)       AS n_flags,
       MIN(raw_value) AS min_raw_value,
       MAX(raw_value) AS max_raw_value
FROM v_red_numbers
GROUP BY rule_code, perspective, disposition
ORDER BY n_flags DESC;

-- ============================================================
-- CONSOLIDATED WORKLIST (reads fact_anomaly_flag, populated by the scorer)
-- ============================================================

-- Ranked master worklist: every flag enriched with vendor/dept/tender names.
DROP VIEW IF EXISTS v_anomaly_worklist;
CREATE VIEW v_anomaly_worklist AS
SELECT f.flag_id, f.score, f.severity, f.confidence,
       f.rule_code, f.family, f.disposition, f.status,
       f.entity_type, f.entity_id,
       f.tender_id, f.vendor_id, v.name AS vendor_name,
       f.dept_id, d.name AS dept_name,
       f.raw_value, f.repaired_value, f.ref_value, f.metric,
       f.evidence, substr(t.description, 1, 70) AS tender_desc
FROM fact_anomaly_flag f
LEFT JOIN dim_vendor v      ON v.vendor_id = f.vendor_id
LEFT JOIN dim_department d  ON d.department_id = f.dept_id
LEFT JOIN fact_tender t     ON t.tender_id = f.tender_id
ORDER BY f.score DESC, f.raw_value DESC;

-- Worklist summary: flag counts / score stats per rule.
DROP VIEW IF EXISTS v_anomaly_summary;
CREATE VIEW v_anomaly_summary AS
SELECT rule_code, family, disposition,
       COUNT(*)                                      AS n_flags,
       ROUND(AVG(score), 3)                          AS avg_score,
       ROUND(MAX(score), 3)                          AS max_score,
       SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_flags
FROM fact_anomaly_flag
GROUP BY rule_code, family, disposition
ORDER BY n_flags DESC;
