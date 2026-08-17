#!/usr/bin/env python
"""Build a slim, read-optimized copy of the eProc DB for the web app / HF deploy.

The full DB (~20 GB) is dominated by raw API payloads in ``raw_response`` and the
``api_call_log`` audit trail. The web app never queries either, so we copy every
object but drop the *data* of those two tables (their empty schema is kept so the
fact-table foreign keys and the view comments stay valid), then VACUUM. The
result is functionally identical for serving and small enough to ship.

Usage:  python scripts/build_web_db.py [SRC=data/bihar_eproc.db] [OUT=data/bihar_web.db]
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time

SRC = sys.argv[1] if len(sys.argv) > 1 else "data/bihar_eproc.db"
OUT = sys.argv[2] if len(sys.argv) > 2 else "data/bihar_web.db"

SKIP_DATA = {"raw_response", "api_call_log"}          # keep schema, drop rows
SKIP_ALL = {"casebook", "casebook_item"}               # web app recreates at startup


def main() -> None:
    if not os.path.exists(SRC):
        sys.exit(f"source not found: {SRC}")
    for f in (OUT, OUT + "-wal", OUT + "-shm"):
        if os.path.exists(f):
            os.remove(f)

    t0 = time.time()
    con = sqlite3.connect(OUT, uri=True)
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("ATTACH DATABASE ? AS src", (f"file:{os.path.abspath(SRC)}?mode=ro",))

    objs = con.execute(
        "SELECT type, name, sql FROM src.sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    tables = [(n, s) for t, n, s in objs if t == "table" and n not in SKIP_ALL]
    indexes = [(n, s) for t, n, s in objs if t == "index" and n not in SKIP_ALL]
    views = [(n, s) for t, n, s in objs if t == "view" and n not in SKIP_ALL]
    triggers = [(n, s) for t, n, s in objs if t == "trigger" and n not in SKIP_ALL]

    for name, sql in tables:
        con.execute(sql)
    for name, sql in tables:
        if name in SKIP_DATA:
            print(f"  schema-only  {name}")
            continue
        n = con.execute(f'INSERT INTO main."{name}" SELECT * FROM src."{name}"').rowcount
        print(f"  copied {n:>9,}  {name}")
    con.commit()

    for name, sql in indexes:
        try:
            con.execute(sql)
        except sqlite3.OperationalError as e:
            print(f"  skip index {name}: {e}")

    # Views may depend on each other; create in repeated passes until stable.
    pending = list(views)
    while pending:
        progressed = []
        for name, sql in pending:
            try:
                con.execute(sql)
            except sqlite3.OperationalError:
                progressed.append((name, sql))
        if len(progressed) == len(pending):
            for name, sql in progressed:
                print(f"  FAILED view {name}")
            break
        pending = progressed
    for name, sql in triggers:
        try:
            con.execute(sql)
        except sqlite3.OperationalError as e:
            print(f"  skip trigger {name}: {e}")
    con.commit()

    con.execute("DETACH DATABASE src")
    print("  VACUUM ...")
    con.execute("VACUUM")
    con.close()

    mb = os.path.getsize(OUT) / 1024 / 1024
    print(f"\nOK  {OUT}  {mb:,.1f} MB  in {time.time() - t0:,.0f}s")


if __name__ == "__main__":
    main()
