"""Pipeline orchestration: wires ingestion + parsing + validation together."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .db import Database
from .analysis.anomaly_scorer import AnomalyScorer
from .http_client import CallRecord, PortalClient
from .ingest.detail import DetailWorker
from .ingest.discovery import DiscoveryCrawler
from .ingest.masters import MasterLoader
from .parsers.authority_parser import AuthorityParser
from .parsers.corrigendum_parser import CorrigendumParser
from .parsers.masters_parser import MasterParser
from .parsers.po_parser import PurchaseOrderParser
from .parsers.sor_parser import SorItemParser
from .parsers.tender_parser import TenderParser
from .settings import Settings
from .validation import Validator, format_report


@dataclass
class Pipeline:
    """High-level driver for the ingestion dependency graph."""

    settings: Settings
    db: Database = field(init=False)
    client: PortalClient = field(init=False)

    def __post_init__(self) -> None:
        self.db = Database(self.settings.database_path)
        self.client = PortalClient(self.settings, call_logger=self._log_call)
        self._bootstrapped = False

    def _log_call(self, record: CallRecord) -> None:
        self.db.log_call(
            record.endpoint, record.method, record.url, record.params,
            record.http_status, record.response_bytes, record.duration_ms,
            record.ok, record.error,
        )

    def _ensure_session(self) -> None:
        if not self._bootstrapped:
            self.client.bootstrap()
            self._bootstrapped = True

    def close(self) -> None:
        self.db.close()

    # -- stages ------------------------------------------------------------
    def init_db(self) -> None:
        self.db.init_schema()
        self.db.rebuild_views()

    def ingest_masters(self) -> dict[str, int]:
        self._ensure_session()
        return MasterLoader(self.client, self.db).run()

    def expand_departments(self, max_calls: int = 4000) -> dict[str, int]:
        self._ensure_session()
        return MasterLoader(self.client, self.db).expand_department_tree(max_calls)

    def discover(self, tabs: tuple[str, ...] | None = None) -> dict[str, int]:
        self._ensure_session()
        tabs = tabs or self.settings.crawl.tabs
        return DiscoveryCrawler(self.client, self.db).run(tabs)

    def ingest_details(
        self, limit: int | None = None, phases: tuple[str, ...] | None = None
    ) -> dict[str, int]:
        self._ensure_session()
        worker = DetailWorker(self.client, self.db, force=self.settings.crawl.force_refetch)
        active = phases or ("details", "purchase_orders", "corrigenda")
        result = {"details": 0, "purchase_orders": 0, "corrigenda": 0}
        if "details" in active:
            result["details"] = worker.fetch_details(limit=limit)
        if "purchase_orders" in active:
            result["purchase_orders"] = worker.fetch_purchase_orders(limit=limit)
        if "corrigenda" in active:
            result["corrigenda"] = worker.fetch_corrigenda(limit=limit)
        return result

    def parse_all(self, only: str | None = None) -> dict[str, Any]:
        """Parse raw payloads into canonical tables.

        ``only`` restricts the work to one phase (masters | tenders |
        purchase_orders | corrigenda) — handy for a fast dimension refresh
        (e.g. re-flattening departments) without re-parsing everything.
        """

        base_url = self.settings.context_path
        result: dict[str, Any] = {}
        if only in (None, "masters"):
            result["masters"] = MasterParser(self.db).run()
        if only in (None, "tenders"):
            result["tenders"] = TenderParser(self.db, base_url).run()
        if only in (None, "purchase_orders"):
            result["purchase_orders"] = PurchaseOrderParser(self.db, base_url).run()
        if only in (None, "sor"):
            result["sor"] = SorItemParser(self.db).run()
        if only in (None, "authorities"):
            result["authorities"] = AuthorityParser(self.db).run()
        if only in (None, "corrigenda"):
            result["corrigenda"] = CorrigendumParser(self.db).run()
        self.db.rebuild_views()
        return result

    def score_anomalies(self) -> dict[str, int]:
        """Consolidate every detector into the ranked fact_anomaly_flag worklist."""
        self.db.init_schema()
        self._backfill_district()
        self.db.rebuild_views()
        result = AnomalyScorer(self.db).run()
        self.db.materialize_views()
        return result

    def _backfill_district(self) -> int:
        """Materialise best-effort Bihar district onto dim_department (drives the
        vendor-district concentration views)."""
        from .geo import district_of
        try:
            self.db.execute("ALTER TABLE dim_department ADD COLUMN district TEXT")
        except Exception:
            pass
        n = 0
        for r in self.db.query("SELECT department_id, name FROM dim_department"):
            dist = district_of(r["name"])
            if dist:
                self.db.execute(
                    "UPDATE dim_department SET district = ? WHERE department_id = ?",
                    (dist, r["department_id"]),
                )
                n += 1
        self.db.commit()
        return n

    def validate(self) -> str:
        return format_report(Validator(self.db).run())

    # -- full run ----------------------------------------------------------
    def run_full(
        self, tabs: tuple[str, ...] | None = None, detail_limit: int | None = None
    ) -> dict[str, Any]:
        report: dict[str, Any] = {}
        self.init_db()
        report["masters_ingested"] = self.ingest_masters()
        report["discovered"] = self.discover(tabs)
        report["details_ingested"] = self.ingest_details(limit=detail_limit)
        report["parsed"] = self.parse_all()
        report["validation"] = self.validate()
        return report
