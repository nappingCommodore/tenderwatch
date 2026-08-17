---
title: Bihar Procurement Integrity Monitor
emoji: 🔎
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# Bihar Procurement Integrity Monitor

A forensic, red-flag analytics dashboard over the public **Bihar e-Procurement**
record — vendor capture, overpricing against the Schedule of Rates, contract
splitting, statistical value outliers and data-integrity artefacts across the
full awarded register.

Every indicator is an **investigative lead, not a finding of wrongdoing**.
Monetary figures use trusted (repaired) values; corrupted source amounts are
flagged and preserved for audit.

## Using it

- **Integrity Monitor** — the consolidated red-flag overview.
- **How to use** — what each flag means and how to verify it.
- **Worklist** — every flag, ranked; filter and drill into cases.
- **Data Explorer** — filter/sort/export the full tender register.
- **Vendors / Departments / Officers** — entity profiles, flags and networks.
- **Map & Trends**, **Network** — geography and related-party clusters.
- **Casebook** — collect evidence into a case and export it (Markdown / JSON / PDF).

## Notes

- This Space serves a **static snapshot** of the analysis (`data/bihar_web.db`),
  a slim copy of the pipeline database with raw payloads removed.
- Casebook and review edits are written to the container filesystem, which is
  **ephemeral** on a standard Space — they reset when the Space rebuilds. Attach
  persistent storage (or an external DB) to keep them.
