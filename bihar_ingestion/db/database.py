"""SQLite database wrapper: connection, schema management, and raw archiving."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from ..utils import now_iso, sha256_hex

_SCHEMA_FILE = Path(__file__).with_name("schema.sql")
_VIEWS_FILE = Path(__file__).with_name("analytics_views.sql")
_ANOMALY_VIEWS_FILE = Path(__file__).with_name("anomaly_views.sql")
_REASONABLENESS_VIEWS_FILE = Path(__file__).with_name("reasonableness_views.sql")
_GRAPH_VIEWS_FILE = Path(__file__).with_name("graph_views.sql")

# Heavy analytics views recomputed on every dashboard query. They are pure
# functions of the batch-loaded facts, so they are snapshotted into physical
# ``mv_`` tables (see Database.materialize_views) that the dashboard reads
# instead. Measured cold cost of the live views: v_vendor_related_party ~76s,
# v_officer_concentration ~6s, the concentration/capture views 1.5-3.3s each.
_MATERIALIZE: tuple[str, ...] = (
    "v_po_value_trusted",
    "v_flag_vendor_capture",
    "v_vendor_concentration",
    "v_dept_vendor_concentration",
    "v_district_vendor_concentration",
    "v_flag_tender_splitting",
    "v_flag_award_vs_estimate",
    "v_flag_sor_overprice",
    "v_officer_concentration",
    "v_vendor_related_party",
    "v_graph_vendor_dept",
)
_MV_INDEX_COLS: tuple[str, ...] = (
    "po_id", "tender_id", "vendor_id", "dept_id", "officer_id", "item_code",
)


class Database:
    """Thin wrapper around a SQLite connection with helpers for this pipeline."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        # Wait for the write lock instead of failing immediately, so parse /
        # expand / crawl can run concurrently (WAL allows one writer at a time).
        self.conn.execute("PRAGMA busy_timeout = 60000")

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def init_schema(self) -> None:
        """Create tables (idempotent) and (re)create analytics views."""

        self.conn.executescript(_SCHEMA_FILE.read_text(encoding="utf-8"))
        self.conn.commit()

    def rebuild_views(self) -> None:
        self.conn.executescript(_VIEWS_FILE.read_text(encoding="utf-8"))
        if _ANOMALY_VIEWS_FILE.exists():
            self.conn.executescript(_ANOMALY_VIEWS_FILE.read_text(encoding="utf-8"))
        if _REASONABLENESS_VIEWS_FILE.exists():
            self.conn.executescript(_REASONABLENESS_VIEWS_FILE.read_text(encoding="utf-8"))
        if _GRAPH_VIEWS_FILE.exists():
            self.conn.executescript(_GRAPH_VIEWS_FILE.read_text(encoding="utf-8"))
        self.conn.commit()

    def materialize_views(self) -> list[str]:
        """Snapshot the heavy analytics views into physical ``mv_`` tables (with
        indexes) so the dashboard reads precomputed results in milliseconds
        instead of recomputing multi-second aggregations on every page load.

        These views are pure functions of the (batch-loaded) fact tables, so a
        snapshot is valid until the next parse/score. All names are internal
        constants — never user input — so the f-string DDL is safe.
        """
        cur = self.conn.cursor()
        built: list[str] = []
        for view in _MATERIALIZE:
            if not cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='view' AND name=?", (view,)
            ).fetchone():
                continue
            mv = "mv_" + view[2:]
            cur.execute(f"DROP TABLE IF EXISTS {mv}")
            cur.execute(f"CREATE TABLE {mv} AS SELECT * FROM {view}")
            have = {r[1] for r in cur.execute(f"PRAGMA table_info({mv})").fetchall()}
            for col in _MV_INDEX_COLS:
                if col in have:
                    cur.execute(f"CREATE INDEX ix_{mv}_{col} ON {mv}({col})")
            built.append(mv)
        self.conn.commit()
        return built

    # -- generic helpers ---------------------------------------------------
    def _run(self, fn: Callable[[], Any]) -> Any:
        """Run a write op, retrying briefly if the database is momentarily locked.

        ``busy_timeout`` already makes SQLite wait for the lock; this adds a
        second layer so a heavily-contended writer never crashes the pipeline.
        """
        delay = 0.5
        for attempt in range(6):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and attempt < 5:
                    time.sleep(delay)
                    delay = min(delay * 2, 5.0)
                    continue
                raise

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self._run(lambda: self.conn.execute(sql, tuple(params)))

    def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> sqlite3.Cursor:
        return self._run(lambda: self.conn.executemany(sql, [tuple(p) for p in seq]))

    def commit(self) -> None:
        self._run(self.conn.commit)

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, tuple(params)).fetchall())

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return row[0] if row else None

    def upsert(self, table: str, row: dict[str, Any], pk: str | tuple[str, ...]) -> None:
        """Insert or update a single row by primary key(s)."""

        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        pk_cols = (pk,) if isinstance(pk, str) else tuple(pk)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in pk_cols)
        conflict = ", ".join(pk_cols)
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict}) DO UPDATE SET {updates}"
            if updates
            else f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict}) DO NOTHING"
        )
        self.execute(sql, tuple(row[c] for c in cols))

    # -- raw archiving -----------------------------------------------------
    def archive_raw(
        self,
        endpoint: str,
        entity_type: str,
        entity_key: str | None,
        payload: Any,
        http_status: int | None = None,
    ) -> int:
        """Store a raw payload and return the raw_response id."""

        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        cur = self.execute(
            """
            INSERT INTO raw_response
                (endpoint, entity_type, entity_key, http_status, payload,
                 content_sha256, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                endpoint,
                entity_type,
                entity_key,
                http_status,
                text,
                sha256_hex(text),
                now_iso(),
            ),
        )
        return int(cur.lastrowid)

    def log_call(
        self,
        endpoint: str,
        method: str,
        url: str,
        params: Any,
        http_status: int | None,
        response_bytes: int | None,
        duration_ms: int | None,
        ok: bool,
        error: str | None,
    ) -> None:
        self.execute(
            """
            INSERT INTO api_call_log
                (endpoint, method, url, params, http_status, response_bytes,
                 duration_ms, ok, error, called_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                endpoint,
                method,
                url,
                json.dumps(params, ensure_ascii=False) if params else None,
                http_status,
                response_bytes,
                duration_ms,
                1 if ok else 0,
                error,
                now_iso(),
            ),
        )

    def log_error(
        self,
        stage: str,
        message: str,
        endpoint: str | None = None,
        entity_key: str | None = None,
        error_type: str | None = None,
    ) -> None:
        self.execute(
            """
            INSERT INTO error_log
                (stage, endpoint, entity_key, error_type, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (stage, endpoint, entity_key, error_type, message, now_iso()),
        )
        self.commit()

    # -- latest raw payload for a given entity (used by parsers) ----------
    def latest_raw(self, entity_type: str, entity_key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT * FROM raw_response
            WHERE entity_type = ? AND entity_key = ?
            ORDER BY id DESC LIMIT 1
            """,
            (entity_type, entity_key),
        ).fetchone()

    def iter_latest_raw(self, entity_type: str) -> Iterator[sqlite3.Row]:
        """Yield the most recent raw payload per entity_key, one row at a time.

        Streams rather than materializing: it fetches the (small) set of row ids
        up front, then reads each payload individually. This keeps memory flat
        even when parsing 100k+ large payloads (a fetchall() here would load
        ~17 GB for the tender set). Safe to commit between yields.
        """

        ids = [
            row[0]
            for row in self.conn.execute(
                """
                SELECT r.id FROM raw_response r
                JOIN (
                    SELECT entity_key, MAX(id) AS max_id
                    FROM raw_response
                    WHERE entity_type = ?
                    GROUP BY entity_key
                ) latest ON latest.max_id = r.id
                """,
                (entity_type,),
            ).fetchall()
        ]
        for rid in ids:
            row = self.conn.execute(
                "SELECT * FROM raw_response WHERE id = ?", (rid,)
            ).fetchone()
            if row is not None:
                yield row
