"""Command-line interface for the Bihar eProcurement ingestion framework."""

from __future__ import annotations

import argparse
import json
import sys

from .orchestrator import Pipeline
from .settings import load_settings, with_overrides


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bedif",
        description="Bihar eProcurement -> SQLite ingestion framework",
    )
    parser.add_argument("-c", "--config", help="Path to a YAML config file")
    parser.add_argument("--db", help="Override the output SQLite database path")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create schema + analytics views")
    sub.add_parser("masters", help="Fetch master/reference data")

    p_dept = sub.add_parser(
        "expand-departments",
        help="Walk the org hierarchy so dim_department covers deep leaf offices",
    )
    p_dept.add_argument("--max-calls", type=int, default=4000,
                        help="Safety cap on hierarchy requests")

    p_disc = sub.add_parser("discover", help="Crawl listing tabs into the work queue")
    p_disc.add_argument("--tabs", help="Comma-separated tabs to crawl")

    p_det = sub.add_parser("details", help="Fetch tender/PO/corrigendum details")
    p_det.add_argument("--limit", type=int, help="Max tenders to process")
    p_det.add_argument("--skip-corrigenda", action="store_true",
                       help="Fetch details + POs only; skip the corrigendum phase")

    sub.add_parser("parse", help="Transform raw payloads into canonical tables")
    p_parse = sub.choices["parse"]
    p_parse.add_argument(
        "--only",
        choices=["masters", "tenders", "purchase_orders", "sor", "authorities", "corrigenda"],
        help="Parse only one phase (e.g. masters, for a fast dept refresh)",
    )
    sub.add_parser("score", help="Score all detectors into the fact_anomaly_flag worklist")
    sub.add_parser("materialize",
                   help="Snapshot heavy analytics views into mv_ tables (dashboard speed)")
    sub.add_parser("validate", help="Run data-quality checks")

    p_run = sub.add_parser("run", help="Run the full pipeline end to end")
    p_run.add_argument("--tabs", help="Comma-separated tabs to crawl")
    p_run.add_argument("--detail-limit", type=int, help="Max tenders to detail-fetch")

    return parser


def _tabs(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    return tuple(t.strip().lower() for t in value.split(",") if t.strip())


def _emit(label: str, payload: object) -> None:
    print(f"== {label} ==")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    settings = load_settings(args.config)
    if args.db:
        from pathlib import Path

        settings = with_overrides(settings, database_path=Path(args.db))

    pipeline = Pipeline(settings)
    try:
        if args.command == "init-db":
            pipeline.init_db()
            print(f"Initialized schema at {settings.database_path}")
        elif args.command == "masters":
            pipeline.init_db()
            _emit("masters ingested", pipeline.ingest_masters())
        elif args.command == "expand-departments":
            pipeline.init_db()
            _emit("departments expanded", pipeline.expand_departments(args.max_calls))
        elif args.command == "discover":
            pipeline.init_db()
            _emit("discovered", pipeline.discover(_tabs(args.tabs)))
        elif args.command == "details":
            phases = ("details", "purchase_orders")
            if not getattr(args, "skip_corrigenda", False):
                phases = phases + ("corrigenda",)
            _emit("details ingested",
                  pipeline.ingest_details(limit=args.limit, phases=phases))
        elif args.command == "parse":
            _emit("parsed", pipeline.parse_all(only=getattr(args, "only", None)))
        elif args.command == "score":
            _emit("anomalies scored", pipeline.score_anomalies())
        elif args.command == "materialize":
            _emit("materialized", {"tables": pipeline.db.materialize_views()})
        elif args.command == "validate":
            print(pipeline.validate())
        elif args.command == "run":
            report = pipeline.run_full(
                tabs=_tabs(args.tabs), detail_limit=args.detail_limit
            )
            _emit("masters ingested", report["masters_ingested"])
            _emit("discovered", report["discovered"])
            _emit("details ingested", report["details_ingested"])
            _emit("parsed", report["parsed"])
            print(report["validation"])
        else:  # pragma: no cover - argparse enforces choices
            return 2
    finally:
        pipeline.db.commit()
        pipeline.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
