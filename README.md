# Bihar eProcurement Data Ingestion Framework (BEDIF)

An automated, resumable pipeline that extracts publicly available procurement
data from the Bihar eProcurement portal (`eproc2.bihar.gov.in`) and normalizes
it into a single **SQLite** database designed for tender/bid analysis.

This is the first data-parsing step for a tender & bid analysis engine. It
covers the portal's public "open area": tender listings, full tender details,
awarded purchase orders, corrigenda, and document metadata. Losing-bidder /
competing-quote data is *not* publicly exposed and is out of scope for this
phase (the schema leaves room to add it later behind authentication).

## What it produces

A layered SQLite database (`data/bihar_eproc.db` by default):

| Layer | Tables / Views | Purpose |
|-------|----------------|---------|
| **Raw** | `raw_response`, `api_call_log`, `crawl_state`, `error_log` | exact API payloads, audit trail, resumable cursors |
| **Queue** | `tender_discovery` | tenders found by the listing crawlers |
| **Dimensions** | `dim_department`, `dim_procurement_category`, `dim_rfq_type`, `dim_rfq_category`, `dim_bid_part`, `dim_vendor`, `dim_currency`, `dim_status` | normalized lookups |
| **Facts** | `fact_tender`, `fact_purchase_order`, `fact_po_item`, `fact_corrigendum`, `fact_document` | analytical grain |
| **Analytics (gold)** | `v_tender_full`, `v_award_summary`, `v_vendor_profile`, `v_department_profile`, `v_po_line_items`, `v_document_inventory`, `v_corrigendum_summary` | ready-to-query views |

Timestamps are stored both as raw epoch-milliseconds (`*_epoch`) and ISO-8601
UTC strings (`*_date`). Money is stored as `REAL` with an accompanying currency
code. Foreign keys are enforced (`PRAGMA foreign_keys = ON`).

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Usage

All commands are exposed through the CLI module:

```powershell
# 1. Create the schema + analytics views
python -m bihar_ingestion.cli init-db

# 2. Fetch master/reference data (departments, categories, etc.)
python -m bihar_ingestion.cli masters

# 3. Crawl the listing tabs into the work queue
python -m bihar_ingestion.cli discover --tabs past,open,cancelled

# 4. Fetch tender detail / purchase order / corrigendum payloads
python -m bihar_ingestion.cli details --limit 100

# 5. Transform archived raw payloads into the canonical tables
python -m bihar_ingestion.cli parse

# 6. Run data-quality checks
python -m bihar_ingestion.cli validate

# ...or run the whole pipeline end to end:
python -m bihar_ingestion.cli run
```

Use `-c config.yaml` to supply configuration and `--db path.db` to override the
output database.

## Configuration

Copy `config.example.yaml` to `config.yaml` and adjust. Key options:

- `portal.root_org_ids` — organizations whose **department hierarchy** is loaded
  into `dim_department` for name enrichment. `538` = "Government of Bihar" is the
  top-level root and its tree already contains every department, so the default
  covers the whole portal. You do **not** enumerate sub-organizations manually.
- `portal.discovery_org_id` — the org the **tender listing** crawl is scoped to.
  `538` (the portal root) returns every tender across all departments, so the
  default crawls the whole portal in one pass. Use `null` for an empty filter
  (equivalent) or a sub-org id to pilot a subset.
- `http.request_delay_seconds` — politeness delay between requests.
- `http.verify_tls` / `http.ca_bundle` — TLS handling. Some government portals
  serve an incomplete certificate chain. If you hit
  `certificate verify failed`, point `ca_bundle` at a PEM file containing the
  portal's intermediate/root certs, or set `verify_tls: false` to bypass
  verification (understand the risk first).
- `pagination.page_size` / `max_pages` — listing page size and page cap.
- `crawl.tabs` — which listing tabs to crawl.

## Crawl scope & scale

Discovery paginates each listing tab via the portal's `startpoint`/`maxRow`
cursor. Verified live: `startpoint` is honoured, so pages march backwards in
time and the crawler pulls **every** available tender, not just the first page.
With `pagination.max_pages: 0` (the default) it runs until the portal returns an
empty page.

Scale, as observed on the live portal (org 538 = Government of Bihar):

- **Past tenders**: ~95,000–99,000 records reaching back to ~**September 2020**
  (~6 years — the full history this eProcurement platform holds; there is no
  10-year data because the system began ~2020).
- At the default `page_size: 100`, a full past-tenders discovery is ~1,000
  requests; detail/PO/corrigendum fetches then run per tender. A complete
  first-time crawl is a multi-hour batch, so it is fully **resumable**
  (`crawl_state` cursors + `detail_fetched`/`po_fetched`/`corr_fetched` flags)
  and can be run in sessions or re-run incrementally.

Tenders that recur at page boundaries (the listing shifts as new tenders are
published mid-crawl) are de-duplicated by the `tender_id` primary key.

## How authentication works

The portal's REST endpoints require:

1. a `JSESSIONID` cookie issued by the HTML entry page, and
2. an `Authorization` bearer token obtained from
   `/rest/login/provideTokenObject`.

The client establishes both automatically on the first request (and re-auths on
`401/403`). The listing endpoints additionally require a JSON filter body and a
custom `Auth-Token: X-Requested-With` header — all handled by the client.

## Documents

Document metadata (label, filename, size, and a constructed
`downloadDynamicAttachment` URL) is captured in `fact_document`. This phase does
**not** download the binaries; `sha256` and `downloaded_at` are left `NULL` for
a future download worker.

## Purchase orders, vendors & line items

The purchase-order API (`getPoDetailsForPastTender`) is mined beyond its header
fields: the PO `templateMap` yields the **awarded vendor's identity**
(`dim_vendor`: company name, EPS/legacy code, GSTIN, PAN/UID, address, city,
state, country) and the **line items** (`fact_po_item`: work ref/description,
UoM, quantity, unit rate, sub-total, tax, total cost). Any PO attachment is
captured in `fact_document` with `source = 'po'`. Competing/losing-bidder quotes
are auth-gated and out of scope for this phase.

## Tests

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Tests parse the recorded HAR fixtures (`tests/fixtures/har/`) and assert known
values (e.g. tender `121339`, its purchase order of ₹331,705.72, its documents,
and the two corrigenda for tender `1001`). Regenerate fixtures from a fresh HAR
with `python scripts/extract_har_fixtures.py`.

## Project layout

```
bihar_ingestion/
  settings.py          # YAML-backed configuration
  api_catalog.yaml     # endpoint registry (verified against the HAR)
  http_client.py       # session, auth, rate limiting, retries, pagination
  db/
    schema.sql         # raw + queue + dimensions + facts
    analytics_views.sql# gold-layer views
    database.py        # connection + raw archiving helpers
  ingest/              # API-calling stages (archive raw payloads)
    masters.py  discovery.py  detail.py
  parsers/             # raw -> canonical transforms (no network)
    masters_parser.py  tender_parser.py  po_parser.py
    corrigendum_parser.py  documents.py  fields.py  _base.py
  validation.py        # data-quality checks
  orchestrator.py      # dependency-graph pipeline driver
  cli.py               # command-line entry point
scripts/
  extract_har_fixtures.py
tests/
```
