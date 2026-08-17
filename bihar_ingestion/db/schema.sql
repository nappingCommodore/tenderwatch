-- Bihar eProcurement Ingestion Framework — canonical SQLite schema
-- =================================================================
-- Layers in a single database file:
--   1. RAW        : exact API payloads + crawl bookkeeping (audit / replay)
--   2. DISCOVERY  : tender work-queue populated by the listing crawlers
--   3. DIMENSIONS : normalized lookup entities (dim_*)
--   4. FACTS      : analytical grain tables (fact_*)
-- Analytics views are defined separately in analytics_views.sql.
--
-- Conventions:
--   * All portal timestamps are epoch milliseconds. We store the raw epoch
--     (*_epoch, INTEGER) and an ISO-8601 UTC string (*_at / *_date, TEXT).
--   * Money is stored as REAL with an accompanying currency code.
--   * Natural keys from the portal are reused as primary keys where stable.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ---------------------------------------------------------------------------
-- 1. RAW LAYER
-- ---------------------------------------------------------------------------

-- One row per archived API payload, keyed by entity so parsers can replay.
CREATE TABLE IF NOT EXISTS raw_response (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint      TEXT    NOT NULL,          -- logical endpoint name
    entity_type   TEXT    NOT NULL,          -- tender | po | corrigendum | master | department | listing
    entity_key    TEXT,                      -- e.g. tender id, or listing cursor
    http_status   INTEGER,
    payload       TEXT    NOT NULL,          -- raw JSON body as received
    content_sha256 TEXT   NOT NULL,
    fetched_at    TEXT    NOT NULL           -- ISO-8601 UTC
);
CREATE INDEX IF NOT EXISTS ix_raw_entity ON raw_response (entity_type, entity_key);
CREATE INDEX IF NOT EXISTS ix_raw_endpoint ON raw_response (endpoint);

-- Audit log of every HTTP call the framework makes.
CREATE TABLE IF NOT EXISTS api_call_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint      TEXT    NOT NULL,
    method        TEXT    NOT NULL,
    url           TEXT    NOT NULL,
    params        TEXT,                       -- JSON-encoded params
    http_status   INTEGER,
    response_bytes INTEGER,
    duration_ms   INTEGER,
    ok            INTEGER NOT NULL DEFAULT 0, -- 1 = success
    error         TEXT,
    called_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_call_endpoint ON api_call_log (endpoint, called_at);

-- Resumable cursor state per listing endpoint.
CREATE TABLE IF NOT EXISTS crawl_state (
    endpoint       TEXT PRIMARY KEY,          -- e.g. discovery:past
    next_startpoint INTEGER NOT NULL DEFAULT 0,
    page_size      INTEGER,
    exhausted      INTEGER NOT NULL DEFAULT 0, -- 1 = fully paged
    last_run_at    TEXT,
    rows_seen      INTEGER NOT NULL DEFAULT 0,
    notes          TEXT
);

-- Structured error log for any stage.
CREATE TABLE IF NOT EXISTS error_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stage       TEXT NOT NULL,               -- discovery | detail | po | corrigendum | parse | ...
    endpoint    TEXT,
    entity_key  TEXT,
    error_type  TEXT,
    message     TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_error_stage ON error_log (stage, created_at);

-- ---------------------------------------------------------------------------
-- 2. DISCOVERY / WORK QUEUE
-- ---------------------------------------------------------------------------

-- Populated by the listing crawlers; drives the detail/PO/corrigendum workers.
CREATE TABLE IF NOT EXISTS tender_discovery (
    tender_id        INTEGER PRIMARY KEY,
    org_tender_id    INTEGER,
    ref_no           TEXT,
    source_tab       TEXT,                    -- open | past | cancelled | upcoming | corrigendum
    listing_status   TEXT,                    -- OPEN | CLOSED | CANCELLED | UPCOMING | CORRIGENDUM
    status_code      INTEGER,                 -- portal numeric status
    org_id           INTEGER,
    dept_id          INTEGER,
    proc_cat_id      INTEGER,
    tender_type_id   INTEGER,
    tender_cat_id    INTEGER,
    description      TEXT,
    publish_epoch    INTEGER,
    close_epoch      INTEGER,
    corrigendum_flag TEXT,
    -- work-tracking flags
    detail_fetched   INTEGER NOT NULL DEFAULT 0,
    po_fetched       INTEGER NOT NULL DEFAULT 0,
    corr_fetched     INTEGER NOT NULL DEFAULT 0,
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_disc_tab ON tender_discovery (source_tab);
CREATE INDEX IF NOT EXISTS ix_disc_pending_detail ON tender_discovery (detail_fetched);

-- ---------------------------------------------------------------------------
-- 3. DIMENSIONS
-- ---------------------------------------------------------------------------

-- Organization / department hierarchy (self-referencing).
CREATE TABLE IF NOT EXISTS dim_department (
    department_id     INTEGER PRIMARY KEY,    -- organizationId
    parent_id         INTEGER,                -- parentId (self-ref)
    name              TEXT,
    code              TEXT,
    address           TEXT,
    storage_path      TEXT,
    identify_string   TEXT,
    poc_name          TEXT,
    poc_email         TEXT,
    poc_phone         TEXT,
    is_active         INTEGER,
    district          TEXT,                   -- best-effort Bihar district from name
    FOREIGN KEY (parent_id) REFERENCES dim_department (department_id)
);
CREATE INDEX IF NOT EXISTS ix_dept_parent ON dim_department (parent_id);

CREATE TABLE IF NOT EXISTS dim_procurement_category (
    proc_cat_id       INTEGER PRIMARY KEY,
    description       TEXT,
    parent_proc_cat_id INTEGER
);

CREATE TABLE IF NOT EXISTS dim_rfq_type (
    rfq_type_id       INTEGER PRIMARY KEY,    -- tender type id (Open/Limited/...)
    description       TEXT,
    tender_type       TEXT,                   -- OPEN | LIMITED
    single_tender_flag TEXT
);

CREATE TABLE IF NOT EXISTS dim_rfq_category (
    rfq_category_id   INTEGER PRIMARY KEY,
    code              TEXT,
    description       TEXT
);

CREATE TABLE IF NOT EXISTS dim_bid_part (
    bid_part_id       INTEGER PRIMARY KEY,    -- id
    bid_part_no       INTEGER,
    code              TEXT,
    description       TEXT,
    max_bid_part_no   INTEGER
);

CREATE TABLE IF NOT EXISTS dim_currency (
    currency_code     TEXT PRIMARY KEY
);

-- Awarded vendors. Identity + location are enriched from the PO templateMap
-- (po_vendor_details); a bare stub (id only) is created first to satisfy FKs.
CREATE TABLE IF NOT EXISTS dim_vendor (
    vendor_id         INTEGER PRIMARY KEY,
    name              TEXT,                   -- vendor_org (Company Name)
    vendor_code       TEXT,                   -- EPS code
    legacy_code       TEXT,
    gstin             TEXT,
    uid_type          TEXT,                   -- PAN / GST / ...
    uid               TEXT,                   -- e.g. PAN number
    address           TEXT,
    city              TEXT,
    state             TEXT,
    country           TEXT
);

-- Status code lookup. Seeded from listing-tab context + observed codes.
CREATE TABLE IF NOT EXISTS dim_status (
    entity            TEXT NOT NULL,          -- tender | po | corrigendum
    status_code       INTEGER NOT NULL,
    label             TEXT,
    PRIMARY KEY (entity, status_code)
);

-- Issuing / approving authorities resolved from tender payloads. CRITICAL:
-- issuing and approving ids are SEPARATE id spaces that collide numerically
-- (id 2049 = 'UPENDRA KUMAR' as issuing but 'Narad Kumar Das' as approving),
-- so the key MUST include role. Within a role, id -> name is 1:1 (verified).
CREATE TABLE IF NOT EXISTS dim_authority (
    role              TEXT    NOT NULL,       -- issuing | approving
    authority_id      INTEGER NOT NULL,
    name              TEXT,
    designation       TEXT,
    org_name          TEXT,
    org_code          TEXT,
    email             TEXT,
    contact_no        TEXT,
    address           TEXT,
    parent_id         INTEGER,
    is_active         INTEGER,
    PRIMARY KEY (role, authority_id)
);

-- ---------------------------------------------------------------------------
-- 4. FACTS
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fact_tender (
    tender_id            INTEGER PRIMARY KEY,
    org_tender_id        INTEGER,
    ref_no               TEXT,
    group_id             INTEGER,
    parent_tender_id     INTEGER,             -- self-ref: rebid / corrigendum lineage
    org_id               INTEGER,
    dept_id              INTEGER,
    tender_type_id       INTEGER,             -- -> dim_rfq_type
    tender_cat_id        INTEGER,
    proc_cat_id          INTEGER,             -- -> dim_procurement_category
    bid_part_no          INTEGER,
    status_code          INTEGER,             -- -> dim_status(entity='tender')
    listing_status       TEXT,
    source_tab           TEXT,
    description          TEXT,
    nit                  TEXT,
    ranking_sequence     TEXT,
    pac_amount           REAL,
    tender_currency      TEXT,                -- -> dim_currency
    bid_currency         TEXT,
    min_bid_no           INTEGER,
    tender_call_no       INTEGER,
    offer_validity_days  INTEGER,
    pki_enabled          TEXT,
    auction_flag         TEXT,
    dept_path            TEXT,                -- parsed from queryString
    issuing_authority_id INTEGER,
    approving_authority_id INTEGER,
    publish_epoch        INTEGER,
    publish_date         TEXT,
    bid_start_epoch      INTEGER,
    bid_start_date       TEXT,
    bid_end_epoch        INTEGER,
    bid_end_date         TEXT,
    bid_open_epoch       INTEGER,
    bid_open_date        TEXT,
    doc_submission_end_epoch INTEGER,
    doc_submission_end_date  TEXT,
    cancel_epoch         INTEGER,
    cancel_date          TEXT,
    cancel_reason        TEXT,
    create_epoch         INTEGER,
    update_epoch         INTEGER,
    raw_response_id      INTEGER,
    first_seen_at        TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    FOREIGN KEY (parent_tender_id) REFERENCES fact_tender (tender_id),
    FOREIGN KEY (dept_id) REFERENCES dim_department (department_id),
    FOREIGN KEY (proc_cat_id) REFERENCES dim_procurement_category (proc_cat_id),
    FOREIGN KEY (tender_type_id) REFERENCES dim_rfq_type (rfq_type_id),
    FOREIGN KEY (raw_response_id) REFERENCES raw_response (id)
);
CREATE INDEX IF NOT EXISTS ix_tender_dept ON fact_tender (dept_id);
CREATE INDEX IF NOT EXISTS ix_tender_proc_cat ON fact_tender (proc_cat_id);
CREATE INDEX IF NOT EXISTS ix_tender_status ON fact_tender (status_code);
CREATE INDEX IF NOT EXISTS ix_tender_publish ON fact_tender (publish_epoch);
CREATE INDEX IF NOT EXISTS ix_tender_parent ON fact_tender (parent_tender_id);

CREATE TABLE IF NOT EXISTS fact_purchase_order (
    po_id                INTEGER PRIMARY KEY, -- epspoId
    org_po_id            INTEGER,
    legacy_po_number     TEXT,
    tender_id            INTEGER NOT NULL,
    vendor_id            INTEGER,
    po_type              TEXT,
    po_ref               TEXT,
    po_value             REAL,
    currency             TEXT,
    item_count           INTEGER,
    is_rate_contract     TEXT,
    status_code          INTEGER,             -- -> dim_status(entity='po')
    quote_ref_no         TEXT,
    parent_po_id         INTEGER,
    amend_serial_no      INTEGER,
    creation_epoch       INTEGER,
    creation_date        TEXT,
    start_epoch          INTEGER,
    start_date           TEXT,
    expiry_epoch         INTEGER,
    expiry_date          TEXT,
    bid_submission_epoch INTEGER,
    bid_submission_date  TEXT,
    raw_response_id      INTEGER,
    first_seen_at        TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    FOREIGN KEY (tender_id) REFERENCES fact_tender (tender_id),
    FOREIGN KEY (vendor_id) REFERENCES dim_vendor (vendor_id),
    FOREIGN KEY (raw_response_id) REFERENCES raw_response (id)
);
CREATE INDEX IF NOT EXISTS ix_po_tender ON fact_purchase_order (tender_id);
CREATE INDEX IF NOT EXISTS ix_po_vendor ON fact_purchase_order (vendor_id);

-- Purchase-order line items (the "WORK DETAILS" / po_rc_qi grid). One row per
-- awarded work/item line within a PO.
CREATE TABLE IF NOT EXISTS fact_po_item (
    po_item_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id                INTEGER NOT NULL,
    tender_id            INTEGER,
    serial_no            INTEGER,             -- indexSerialNo within the PO
    table_primary_key_id INTEGER,             -- portal item id (tablePrimaryKeyId)
    item_code            TEXT,                -- Work Ref No.
    item_name            TEXT,                -- Work Description
    uom                  TEXT,
    quantity             REAL,
    unit_price_rate      REAL,
    sub_total            REAL,
    total_tax            REAL,
    total_cost           REAL,
    remarks              TEXT,
    raw_response_id      INTEGER,
    first_seen_at        TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    UNIQUE (po_id, serial_no),
    FOREIGN KEY (po_id) REFERENCES fact_purchase_order (po_id),
    FOREIGN KEY (tender_id) REFERENCES fact_tender (tender_id),
    FOREIGN KEY (raw_response_id) REFERENCES raw_response (id)
);
CREATE INDEX IF NOT EXISTS ix_po_item_po ON fact_po_item (po_id);
CREATE INDEX IF NOT EXISTS ix_po_item_tender ON fact_po_item (tender_id);

-- Tender rate-contract estimate lines (br_rfq_item_rc): the Schedule-of-Rates
-- (SOR) estimated rate per item, the reasonableness benchmark against the
-- awarded unit_price_rate in fact_po_item (joined on tender_id + item_code).
CREATE TABLE IF NOT EXISTS fact_sor_item (
    sor_item_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id         INTEGER NOT NULL,
    item_code         TEXT,
    item_name         TEXT,
    uom               TEXT,                   -- readable label on tender side (e.g. "each")
    quantity          REAL,
    sor_rate          REAL,                   -- Schedule of Rates estimated rate
    estimated_price   REAL,                   -- estimat_price (qty * sor_rate)
    mandatory         TEXT,
    raw_response_id   INTEGER,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    UNIQUE (tender_id, item_code),
    FOREIGN KEY (tender_id) REFERENCES fact_tender (tender_id),
    FOREIGN KEY (raw_response_id) REFERENCES raw_response (id)
);
CREATE INDEX IF NOT EXISTS ix_sor_tender ON fact_sor_item (tender_id);

-- Consolidated anomaly worklist: one row per (rule, entity) across ALL
-- detectors (artifacts, reasonableness, network). Holds EVERY flagged case
-- (not a top-N); ranking is a query concern (ORDER BY score). A rebuild
-- upserts metrics/evidence but PRESERVES the human `status` + `created_at`.
CREATE TABLE IF NOT EXISTS fact_anomaly_flag (
    flag_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_code      TEXT NOT NULL,          -- e.g. A1_digit_doubled, SOR_overprice
    family         TEXT NOT NULL,          -- artifact | reasonableness | network
    entity_type    TEXT NOT NULL,          -- po | po_item | po_line | department | vendor_pair
    entity_id      TEXT NOT NULL,          -- heterogeneous natural key (as text)
    tender_id      INTEGER,
    vendor_id      INTEGER,
    dept_id        INTEGER,
    severity       REAL NOT NULL,          -- 0..1 magnitude
    confidence     REAL NOT NULL,          -- 0..1 how sure it is real (proof vs heuristic)
    score          REAL NOT NULL,          -- severity * confidence (ranking key)
    raw_value      REAL,                   -- the suspicious number
    repaired_value REAL,                   -- trusted/repaired value if any
    ref_value      REAL,                   -- benchmark/base/estimate compared against
    metric         REAL,                   -- rule-specific ratio / share
    evidence       TEXT,                   -- human-readable explanation
    disposition    TEXT NOT NULL,          -- quarantine | review
    status         TEXT NOT NULL DEFAULT 'open',  -- open | confirmed | dismissed
    review_note    TEXT,                   -- analyst note (set from the dashboard)
    reviewed_at    TEXT,                   -- when status/note was last set
    reviewed_by    TEXT,                   -- who reviewed
    created_at     TEXT NOT NULL,
    UNIQUE (rule_code, entity_type, entity_id)
);
CREATE INDEX IF NOT EXISTS ix_flag_score  ON fact_anomaly_flag (score DESC);
CREATE INDEX IF NOT EXISTS ix_flag_rule   ON fact_anomaly_flag (rule_code);
CREATE INDEX IF NOT EXISTS ix_flag_vendor ON fact_anomaly_flag (vendor_id);
CREATE INDEX IF NOT EXISTS ix_flag_dept   ON fact_anomaly_flag (dept_id);
CREATE INDEX IF NOT EXISTS ix_flag_status ON fact_anomaly_flag (status);

CREATE TABLE IF NOT EXISTS fact_corrigendum (
    corrigendum_id       INTEGER PRIMARY KEY, -- corrId
    org_corrigendum_id   INTEGER,
    tender_id            INTEGER NOT NULL,
    group_id             INTEGER,
    version_no           INTEGER,
    ref_no               TEXT,
    description          TEXT,
    status_code          INTEGER,
    template_group_ids   TEXT,
    attach_file          TEXT,
    file_name            TEXT,
    bidder_modification_required TEXT,
    create_epoch         INTEGER,
    create_date          TEXT,
    update_epoch         INTEGER,
    update_date          TEXT,
    raw_response_id      INTEGER,
    first_seen_at        TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    FOREIGN KEY (tender_id) REFERENCES fact_tender (tender_id),
    FOREIGN KEY (raw_response_id) REFERENCES raw_response (id)
);
CREATE INDEX IF NOT EXISTS ix_corr_tender ON fact_corrigendum (tender_id);

-- Document inventory (metadata + download URL only in this phase).
CREATE TABLE IF NOT EXISTS fact_document (
    document_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id            INTEGER NOT NULL,
    source               TEXT,                -- tender | po | corrigendum
    label                TEXT,                -- group / field label (NIT, BOQ, ...)
    field_code           TEXT,
    template_group_id    INTEGER,
    filename             TEXT,
    relative_path        TEXT,                -- used as downloadDynamicAttachment relativePath
    download_url         TEXT,
    file_size_bytes      INTEGER,
    mime_type            TEXT,
    sha256               TEXT,                -- NULL until a download phase populates it
    downloaded_at        TEXT,                -- NULL in this phase
    raw_response_id      INTEGER,
    first_seen_at        TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    UNIQUE (tender_id, template_group_id, filename, relative_path),
    FOREIGN KEY (tender_id) REFERENCES fact_tender (tender_id),
    FOREIGN KEY (raw_response_id) REFERENCES raw_response (id)
);
CREATE INDEX IF NOT EXISTS ix_doc_tender ON fact_document (tender_id);
