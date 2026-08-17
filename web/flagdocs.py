"""Human-facing documentation for the anomaly rules and the investigative
workflow. Kept as data so the /guide page can render it and merge live counts
from v_anomaly_summary. Descriptions mirror bihar_ingestion/analysis/anomaly_scorer.py.
"""

from __future__ import annotations

# How the app is used, step by step.
WORKFLOW = [
    ("Start on the Integrity Monitor",
     "The overview's six signal sections show where the strongest patterns are — vendor "
     "capture, overpricing, splitting, geography and data integrity. Every bar links straight "
     "to the entity or case behind it."),
    ("Triage in the Worklist",
     "Every flag from every detector, ranked by score. Filter by rule, family, status or "
     "minimum score, or search a vendor / department. A higher score means bigger magnitude "
     "AND higher confidence — work from the top down."),
    ("Verify in the Case file",
     "Open a tender to see its awards, line-items-vs-Schedule-of-Rates, existing flags and "
     "documents. This is where you confirm a flag against the actual numbers instead of taking "
     "it on faith."),
    ("Follow the entities",
     "Click any vendor, department or officer to see their whole footprint: concentration, "
     "their flags, related parties, and an interactive ego-network of who they transact with."),
    ("Explore and export",
     "The Data Explorer filters the full tender register and exports CSV — build your own view, "
     "or assemble an evidence pack for a specific department, vendor or time window."),
    ("Record a decision",
     "On a case, mark each flag confirmed or dismissed with a note. Your decision survives "
     "re-scoring, so the worklist becomes an audit trail rather than a throwaway list."),
]

# How to read the score / status of a flag.
SCORE_NOTES = [
    ("Score = severity × confidence",
     "Severity (0–1) grows with the anomaly's magnitude — how many times the SOR rate, how "
     "large the value vs peers. Confidence (0–1) is how sure we are it is a real anomaly rather "
     "than noise. A mathematically-provable data artifact scores high confidence; a soft "
     "heuristic like contract-splitting scores lower on purpose."),
    ("Severity bands",
     "As a rule of thumb: ≥ 0.60 is a strong signal (red), 0.30–0.60 is worth a look (amber), "
     "below 0.30 is a weak pointer. Sort by score, but always read the evidence line."),
    ("Status lifecycle",
     "Every flag starts open. As you review it you set confirmed (a real red flag worth "
     "escalating) or dismissed (explained / benign). Re-running the scorer never overwrites "
     "your status."),
    ("Disposition: quarantine vs review",
     "Quarantine = an impossible value we set aside and repair (data quality). Review = an "
     "internally-consistent value that needs a human to judge. We never delete anything — the "
     "raw payload is always preserved."),
    ("Raw vs trusted value",
     "Where a stored amount is corrupt, we keep the raw figure and compute a trusted (repaired) "
     "one. All money in the app uses trusted values; the raw is shown on the case so you can "
     "see exactly what was changed."),
]

# Practical playbook: how a person actually finds red flags.
PLAYBOOK = [
    ("Rank by score, but read the evidence",
     "Score orders the queue; the one-line evidence tells you what actually fired and on which "
     "entity. Never act on the number alone."),
    ("Separate corruption from signal first",
     "Artifact flags (the A-family) are usually data-entry problems. Clear or repair them first "
     "so a doubled digit doesn't masquerade as a ₹2-crore overrun."),
    ("One flag is a lead — stacked flags are a case",
     "The strongest cases combine signals: a department with vendor capture whose dominant "
     "vendor is also a related-party cluster and shows monthly splitting bursts is far more "
     "compelling than any single large number."),
    ("Verify at the source",
     "Open the case and check line items, the SOR comparison and documents before calling "
     "something real. The raw payload is preserved for exactly this cross-check."),
    ("Look for repetition and regionality",
     "The same item awarded at 100×+ its SOR across many vendors in one area, or award bursts "
     "that stop just under a round approval ceiling, are stronger than a lone big value."),
    ("Decide, and write down why",
     "Confirm or dismiss with a short rationale. That turns the worklist into a defensible "
     "trail and stops the same lead being re-litigated later."),
]

FAMILY_DOCS = {
    "artifact": ("Data integrity", "#e0a53d",
                 "A recorded value that is internally impossible or malformed. We never delete it: "
                 "the raw value is preserved and a repaired value is used for analysis. Usually a "
                 "data-quality issue — but it can hide a real transaction, so it is still flagged."),
    "reasonableness": ("Cost & value", "#f0553d",
                       "The value is internally consistent but suspicious on its economics — priced "
                       "far above the Schedule of Rates, far from the estimate, or a statistical "
                       "extreme for its procurement category."),
    "network": ("Relationships", "#5aa9e6",
                "Patterns across vendors, departments and officers — one vendor dominating, distinct "
                "records sharing an identity, or many small awards that look like one split contract."),
}

# One entry per rule_code, grouped by family for display (family, [rule_codes]).
GROUPS = [
    ("reasonableness", ["SOR_overprice", "V3_award_vs_estimate", "S1_value_outlier"]),
    ("network", ["N1_vendor_capture", "P2_tender_splitting", "N4_related_party",
                 "N3_vendor_district_concentration"]),
    ("artifact", ["A1_digit_doubled", "A2_pct_tax_line", "A3_scale_error",
                  "A4_repeated_digit", "A5_epoch_as_value"]),
]

FLAG_DOCS = {
    "SOR_overprice": {
        "title": "Overpricing vs Schedule of Rates",
        "what": "An item was awarded at 2× or more the tender's OWN Schedule-of-Rates estimate for "
                "that item. The sharpest cost signal in the dataset, and it works even on lump-sum "
                "tenders because it compares awarded vs estimated for the same line.",
        "how": "awarded_rate ÷ sor_rate ≥ 2, matched on (tender, item code); Re-1 placeholder SOR "
               "rates are excluded. Severity scales with the multiple.",
        "verify": "Open the case → the line-item table shows awarded vs SOR and the ×SOR multiple. "
                  "Read the item name — is it genuinely the same scope? A 100–300× soak-pit / "
                  "tubewell cluster recurring across vendors in one region is a strong lead. Dismiss "
                  "if the SOR rate is a placeholder or the items differ.",
    },
    "V3_award_vs_estimate": {
        "title": "Award far from estimate",
        "what": "The awarded value is a large multiple of the pre-tender estimate (overrun ≥ 3×) or "
                "far below it (lowball ≤ 0.4×). Either can signal manipulation, poor estimation, or "
                "a corrupt value.",
        "how": "trusted award value ÷ estimate (PAC); overruns and lowballs are flagged separately. "
               "Confidence 0.5 — many legitimate causes exist.",
        "verify": "First rule out data corruption (A-family). Then check line items and SOR. A clean, "
                  "itemised award many times the estimate with no artifact flag is a genuine review "
                  "case; a deep lowball can be a front-loaded or loss-leader bid.",
    },
    "S1_value_outlier": {
        "title": "Statistical value outlier",
        "what": "An award that is both in the top 1% of its procurement category by value AND at "
                "least 10× the category's median — a statistical extreme for its peer group.",
        "how": "Per category (with ≥ 100 awards): value in the top 1% percentile AND ≥ 10× the "
               "category median. Confidence 0.4; severity scales with the ×median multiple.",
        "verify": "Open the case: is it a legitimate large project, an artifact, or an unexplained "
                  "multiple of its peers? A clean ₹49 Cr award at 200× its category median is a real "
                  "review case; always rule out corruption first.",
    },
    "N1_vendor_capture": {
        "title": "Vendor capture (department)",
        "what": "One vendor won 60% or more of a department's awarded value across at least five "
                "awards — competition that may exist only on paper.",
        "how": "Per department: top-vendor value share ≥ 0.60 over ≥ 5 awards. Severity = the share "
               "itself; the department page also shows the HHI concentration index.",
        "verify": "Open the department profile: look at the vendor bar chart and HHI. Is the work "
                  "specialised (few possible suppliers) or a commodity that should be competitive? "
                  "Persistent 80–93% capture on commodity work is a strong red flag. Cross-check the "
                  "dominant vendor's related-party cluster.",
    },
    "P2_tender_splitting": {
        "title": "Contract splitting (structuring)",
        "what": "Four or more sub-₹1 Cr awards to the SAME vendor from the SAME department in a "
                "SINGLE month, together exceeding ₹50 L — the classic pattern of splitting one job "
                "to stay under an approval threshold.",
        "how": "(department, vendor, month) with ≥ 4 awards, every award < ₹1 Cr, and total > ₹50 L. "
               "A heuristic (no official ₹ thresholds), so confidence is a deliberate 0.4; severity "
               "grows with the count.",
        "verify": "Filter the Data Explorer by that department and vendor: are these really one "
                  "divisible job? Repeated 15–24 award bursts just under a round ceiling are a strong "
                  "lead. Sharpen it if you know the real approval-tier ₹ thresholds.",
    },
    "N4_related_party": {
        "title": "Related parties (shared PAN / GSTIN)",
        "what": "Two distinct vendor records share a PAN or GSTIN — the same or a related legal "
                "entity appearing under more than one name.",
        "how": "Vendors joined on an identical PAN or GSTIN; pairs are grouped into clusters on the "
               "Network page. Confidence 0.7.",
        "verify": "Network page → open the cluster. If the members jointly win in the SAME "
                  "department, that is possible bid-rigging or splitting under one identity. Benign "
                  "if it is a genuine parent/branch with separate legitimate roles.",
    },
    "N3_vendor_district_concentration": {
        "title": "District concentration (weak signal)",
        "what": "The single biggest vendor holds 15% or more of a district's awards (over ≥ 20 "
                "awards). Deliberately low-confidence: districts pool many vendors, so shares dilute "
                "and this rarely proves capture on its own.",
        "how": "Top-vendor share ≥ 0.15 over ≥ 20 district awards. Confidence 0.3 — real capture "
               "shows at DEPARTMENT level (see vendor capture).",
        "verify": "Use as a pointer, not proof. It matters when the district's top vendor also shows "
                  "department capture or a related-party cluster; on its own, 15–24% is often normal.",
    },
    "A1_digit_doubled": {
        "title": "Doubled amount (digit-doubling)",
        "what": "The amount was stored with its digits written twice — e.g. ₹19,68,925 recorded as "
                "19689251968925. A data-capture corruption, not a real payment.",
        "how": "Fires when the stored value equals its own first half repeated. The repaired (true) "
               "value is kept beside the raw one. Confidence 0.95.",
        "verify": "On the case, compare raw vs trusted value against the estimate and line items — the "
                  "trusted figure should sit near them. Quarantine as an artifact; it is only a red "
                  "flag if the repaired value is itself unreasonable.",
    },
    "A2_pct_tax_line": {
        "title": "Impossible tax / percentage line",
        "what": "A line labelled as GST / tax / percentage whose amount is larger than the base it "
                "applies to — impossible for a true percentage.",
        "how": "Line matches gst / % / tax and its total exceeds the real work lines (base > 0). The "
               "trusted PO value recomputes that line as percentage × base. Confidence 0.95.",
        "verify": "In the line-item table, compare the flagged line to the work lines. If the 'tax' "
                  "dwarfs the works it is a corrupt cell — use the repaired total. A red flag only if "
                  "the award is still unreasonable after repair.",
    },
    "A3_scale_error": {
        "title": "Possible scale / paise error",
        "what": "The PO value is a suspicious round multiple of the estimate, as if units slipped "
                "(paise vs rupees, or a ×100 / ×1000 scale error).",
        "how": "po_value ÷ estimate lands in a scale-error band. Confidence 0.6 — it can also be a "
               "genuine overrun.",
        "verify": "Compare against line items and SOR. If only the PO header is inflated by a round "
                  "factor while the items are sane, it is a scale artifact; if the items agree with "
                  "the big number, treat it as a real cost anomaly.",
    },
    "A4_repeated_digit": {
        "title": "Repeated-digit placeholder",
        "what": "The amount is all identical digits (e.g. 1111111 or 9999999) — a placeholder, not a "
                "real figure.",
        "how": "Raw value is a run of a single repeated digit. Confidence 0.8.",
        "verify": "Check whether a real value exists in the line items or documents. Usually a "
                  "placeholder to quarantine; escalate only if it hides a real transaction.",
    },
    "A5_epoch_as_value": {
        "title": "Timestamp used as an amount",
        "what": "A value in the millisecond-epoch range, suggesting a date was mistakenly stored as "
                "an amount. No matches in the current dataset.",
        "how": "Value falls in the ms-epoch band. Confidence 0.5.",
        "verify": "Cross-check the field against the tender's dates. A data-hygiene issue rather than "
                  "corruption of money.",
    },
}
