"""Block until the background crawl finishes discovery, then print per-tab
totals. Discovery is complete once the pipeline starts issuing detail calls.

Read-only + WAL-safe; does not touch the running writer. Run:
    python scripts/monitor_discovery.py
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

DB = "file:data/bihar_eproc.db?mode=ro"
POLL_SECONDS = 20
TIMEOUT_SECONDS = 60 * 60


def connect() -> sqlite3.Connection:
    return sqlite3.connect(DB, uri=True, timeout=30)


def snapshot(c: sqlite3.Connection) -> tuple[int, int, list, int]:
    discovered = c.execute("SELECT COUNT(*) FROM tender_discovery").fetchone()[0]
    details = c.execute(
        "SELECT COUNT(*) FROM api_call_log WHERE endpoint LIKE 'detail:%'"
    ).fetchone()[0]
    per_tab = list(
        c.execute(
            "SELECT source_tab, COUNT(*) FROM tender_discovery GROUP BY source_tab "
            "ORDER BY source_tab"
        )
    )
    exhausted = c.execute(
        "SELECT COUNT(*) FROM crawl_state WHERE endpoint LIKE 'discovery:%' AND exhausted=1"
    ).fetchone()[0]
    return discovered, details, per_tab, exhausted


def report(c: sqlite3.Connection, header: str) -> None:
    discovered, _, per_tab, _ = snapshot(c)
    print(f"\n{header}")
    print(f"  total discovered: {discovered}")
    for tab, n in per_tab:
        print(f"    {tab:12s} {n}")
    # publish-date span, to show historical depth actually captured
    span = c.execute(
        "SELECT MIN(publish_epoch), MAX(publish_epoch) FROM tender_discovery "
        "WHERE publish_epoch IS NOT NULL"
    ).fetchone()
    if span and span[0]:
        f = lambda e: datetime.fromtimestamp(e / 1000, tz=timezone.utc).date()
        print(f"  publish span: {f(span[0])} .. {f(span[1])}")


def main() -> None:
    start = time.monotonic()
    c = connect()
    expected_tabs = 5  # open, past, cancelled, upcoming, corrigendum
    print("monitoring discovery... (exits when all tabs are exhausted)")
    while True:
        discovered, details, per_tab, exhausted = snapshot(c)
        tabs = " ".join(f"{t}={n}" for t, n in per_tab)
        print(f"[{int(time.monotonic()-start):4d}s] discovered={discovered:6d} "
              f"exhausted_tabs={exhausted}/{expected_tabs}  {tabs}")
        if exhausted >= expected_tabs:
            report(c, "=== DISCOVERY COMPLETE — final per-tab totals ===")
            return
        if time.monotonic() - start > TIMEOUT_SECONDS:
            report(c, "=== TIMEOUT — discovery not finished; current totals ===")
            return
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
