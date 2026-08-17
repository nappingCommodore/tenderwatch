

The updated HAR confirms several important points:

* The portal is API-driven rather than HTML-driven.
* Each tab corresponds to a separate REST endpoint.
* Tender Details, Purchase Orders, Corrigenda, Department hierarchy, and Master data are exposed independently.
* The document popup is a second-stage workflow (click Action → popup → download links), which should be treated as a separate extraction module rather than being coupled to tender extraction.

---

# Bihar eProcurement Data Ingestion Framework (BEDIF)

## Objective

Develop an automated, resilient, resumable ingestion framework capable of extracting all publicly accessible procurement information from the Bihar eProcurement portal into the Bihar Procurement Canonical Data Model (BPCDM).

The framework should:

* Discover all tenders
* Extract all tender metadata
* Download all associated documents
* Track historical changes
* Normalize entities
* Store raw API responses
* Produce analytics-ready canonical tables

The ingestion system should be idempotent, version-aware, and support incremental synchronization.

---

# Overall Architecture

```text
                  Scheduler
                      │
                      ▼
            Discovery Orchestrator
                      │
      ┌───────────────┼────────────────┐
      ▼               ▼                ▼
 Master Loader   Tender Discovery   Incremental Sync
      │               │                │
      └───────────────┼────────────────┘
                      ▼
               Tender Queue
                      │
     ┌────────────────┼──────────────────┐
     ▼                ▼                  ▼
 Tender Parser    PO Parser      Corrigendum Parser
     │                │                  │
     └────────────────┼──────────────────┘
                      ▼
              Document Extractor
                      │
                      ▼
             PDF Download Manager
                      │
                      ▼
            Raw JSON + File Storage
                      │
                      ▼
          Canonical Transformation
                      │
                      ▼
                 DuckDB Database
                      │
                      ▼
            Feature Engineering Layer
```

---

# Module 1 — Master Data Loader

Runs once initially and periodically thereafter.

### APIs

```
/rest/organization/loadAllDeptWithParent

/rest/master/getMasterListForOpenareaTenderListing

/rest/master/showRfqCategoryRfqTypeProcCatBidPartList

/rest/master/getDefaultDateTimeFormat
```

Produces:

```
dim_department

dim_procurement_category

dim_bid_type

dim_rfq

dim_status

dim_currency
```

---

# Module 2 — Tender Discovery

The HAR confirms these discovery endpoints.

```
getTenderList

getPastTenders

getCancelTenderList

getCorrigendumTenders

getUpcommingTenders
```

Treat every tab independently.

## Current Tender

```
getTenderList
```

Status

```
OPEN
```

---

## Past Tender

```
getPastTenders
```

Status

```
CLOSED
```

---

## Cancelled

```
getCancelTenderList
```

Status

```
CANCELLED
```

---

## Corrigendum

```
getCorrigendumTenders
```

Status

```
CORRIGENDUM
```

---

## Upcoming

```
getUpcommingTenders
```

Status

```
UPCOMING
```

---

Each discovered record should only contain

```
Tender ID

Reference No

Status

Publish Date

Closing Date

Department

Organization

Category
```

Nothing more.

These populate the discovery queue.

---

# Module 3 — Tender Detail Extraction

For every Tender ID

```
previewTenderByTenderId
```

Store the response exactly as received.

```
raw_json/

     tender/

           121339.json
```

Never modify.

---

Parser produces

```
fact_tender

fact_tender_detail

fact_schedule

fact_contact

fact_fee

fact_work
```

---

# Module 4 — Purchase Order Extraction

```
getPoDetailsForPastTender
```

This should become

```
fact_purchase_order
```

Store

```
PO Number

Agreement

Award Date

Contract Value

Vendor

Department

Status

Validity
```

---

# Module 5 — Corrigendum Extraction

```
getPublishedCorrigendumByTenderId
```

Every corrigendum becomes

```
fact_corrigendum
```

Never overwrite.

---

# Module 6 — Document Extraction

This is where the popup shown in your screenshot comes into play.

Workflow:

```
Tender

↓

Click Action

↓

Popup

↓

Document List

↓

Download URL
```

The popup itself is not the data.

It is simply exposing another document collection.

Treat this separately.

---

## Document Metadata

Each row in the popup

```
Label

Filename

Download URL
```

Example

```
NIT

Scope of Work

Agreement

Escrow

Guarantee

KPI

EHS

BOQ

Specifications

Drawings
```

Each becomes

```
fact_document
```

Columns

```
document_id

tender_id

label

filename

mime_type

download_url

sha256

download_time

file_size
```

---

# Module 7 — PDF Download Worker

Independent service.

Input

```
download_url
```

Output

```
storage/

     tender/

          121339/

                NIT.pdf

                BOQ.xlsx

                Scope.pdf

                KPI.pdf
```

Maintain hashes.

```
sha256
```

Never overwrite.

Version instead.

---

# Module 8 — Canonical Parser

Raw JSON

↓

Flatten

↓

Normalize

↓

DuckDB

Parser should never call APIs.

Parser only consumes archived JSON.

---

# Database Layout

## Raw Layer

```
api_request

api_response

download_log

error_log
```

---

## Bronze Layer

Exactly mirrors API.

```
raw_tender

raw_po

raw_department

raw_corrigendum

raw_document
```

---

## Silver Layer

Canonical.

```
fact_tender

fact_po

fact_document

fact_corrigendum

dim_department

dim_category

dim_vendor
```

---

## Gold Layer

Analytics

```
vendor_profile

department_profile

district_profile

risk_features

tender_features
```

---

# Crawl Strategy

## Initial Crawl

```
Masters

↓

Current

↓

Upcoming

↓

Past

↓

Cancelled

↓

Corrigendum
```

Then

```
Tender Details

↓

PO

↓

Documents
```

---

## Incremental

Every day

```
Current

Upcoming

Corrigendum

Cancelled
```

Every week

```
Past
```

---

# Agent Responsibilities

## Agent 1 — API Discovery

Responsibilities

* Maintain endpoint catalog.
* Detect new APIs.
* Version request/response schemas.
* Validate parameter changes.

Deliverable

```
api_catalog.yaml
```

---

## Agent 2 — Discovery Worker

Responsibilities

* Call listing APIs.
* Insert/update Tender IDs.
* Detect new tenders.
* Handle pagination.

---

## Agent 3 — Detail Worker

Responsibilities

* Download tender details.
* Archive raw JSON.
* Queue dependent tasks.

---

## Agent 4 — PO Worker

Responsibilities

* Retrieve purchase order details.
* Normalize contracts.
* Link to tender.

---

## Agent 5 — Document Worker

Responsibilities

* Resolve popup metadata.
* Extract document URLs.
* Queue downloads.

---

## Agent 6 — Download Worker

Responsibilities

* Download files.
* Verify checksums.
* Retry failures.
* Organize storage.

---

## Agent 7 — Parser

Responsibilities

* Convert raw JSON into canonical schema.
* Normalize fields.
* Populate DuckDB.

---

## Agent 8 — Validation

Responsibilities

* Detect duplicates.
* Validate foreign keys.
* Identify missing mandatory fields.
* Produce quality reports.

---

# Recommended Project Structure

```text
bihar-ingestion/

├── api/
│   ├── discovery/
│   ├── masters/
│   ├── tender/
│   ├── purchase_order/
│   ├── corrigendum/
│   └── documents/
│
├── parsers/
│
├── download/
│
├── storage/
│   ├── raw_json/
│   ├── documents/
│   └── logs/
│
├── database/
│
├── scheduler/
│
├── validation/
│
├── feature_engineering/
│
└── analytics/
```

# One enhancement I'd make

Based on both the HAR and the document popup, I would introduce a **dependency graph** rather than a linear crawler:

```text
Master APIs
      │
      ▼
Tender Discovery
      │
      ▼
Tender Detail
 ┌────┴──────────┐
 ▼               ▼
PO         Corrigendum
 │               │
 └──────┬────────┘
        ▼
 Document Inventory
        ▼
 PDF/BOQ Downloader
        ▼
 OCR & Text Extraction (Future)
        ▼
 Canonical Database
```

This architecture keeps each concern isolated, supports retries at any stage, and makes it easy to scale to other state procurement portals later by replacing only the portal-specific API adapters while preserving the downstream processing pipeline.
