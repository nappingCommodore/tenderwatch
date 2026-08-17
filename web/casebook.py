"""Analyst 'casebook': collect flags, tenders, entities and notes into a case
you build over time, then export. Stored in its own tables in the same SQLite
DB (created on demand, independent of the pipeline schema). The single write
path for the web app besides flag review.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .data import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS casebook (
    case_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    summary    TEXT,
    status     TEXT NOT NULL DEFAULT 'open',   -- open | complete
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS casebook_item (
    item_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL,          -- flag | tender | vendor | department | officer | note
    ref_id     TEXT,                   -- referenced id (NULL for a free note)
    label      TEXT,                   -- label captured when added
    note       TEXT,                   -- analyst note / evidence
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_casebook_item_case ON casebook_item(case_id);
"""


def _rw() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def init() -> None:
    con = _rw()
    try:
        con.executescript(_SCHEMA)
        # Migration for DBs created before the 'position' column existed.
        cols = {r[1] for r in con.execute("PRAGMA table_info(casebook_item)").fetchall()}
        if "position" not in cols:
            con.execute("ALTER TABLE casebook_item ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
            con.execute("UPDATE casebook_item SET position = item_id WHERE position = 0")
        con.commit()
    finally:
        con.close()


def _rows(cur) -> list[dict[str, Any]]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# -- cases --------------------------------------------------------------------

def create(title: str, summary: str = "") -> int:
    con = _rw()
    try:
        cur = con.execute("INSERT INTO casebook (title, summary) VALUES (?, ?)",
                          (title.strip() or "Untitled case", summary.strip()))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def get(case_id: int) -> dict[str, Any] | None:
    con = _rw()
    try:
        rows = _rows(con.execute("SELECT * FROM casebook WHERE case_id=?", (case_id,)))
        if not rows:
            return None
        case = rows[0]
        case["items"] = _rows(con.execute(
            "SELECT * FROM casebook_item WHERE case_id=? ORDER BY position, item_id", (case_id,)))
        case["n_items"] = len(case["items"])
        return case
    finally:
        con.close()


def listing() -> list[dict[str, Any]]:
    con = _rw()
    try:
        return _rows(con.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM casebook_item i WHERE i.case_id=c.case_id) "
            "AS n_items FROM casebook c ORDER BY c.updated_at DESC"))
    finally:
        con.close()


def brief(case_id: int) -> dict[str, Any] | None:
    """Lightweight lookup for the active-case chip (title + item count)."""
    con = _rw()
    try:
        rows = _rows(con.execute(
            "SELECT case_id, title, status, "
            "(SELECT COUNT(*) FROM casebook_item i WHERE i.case_id=casebook.case_id) AS n_items "
            "FROM casebook WHERE case_id=?", (case_id,)))
        return rows[0] if rows else None
    finally:
        con.close()


def _touch(con, case_id: int) -> None:
    con.execute("UPDATE casebook SET updated_at=datetime('now') WHERE case_id=?", (case_id,))


def set_meta(case_id: int, title: str, summary: str) -> None:
    con = _rw()
    try:
        con.execute("UPDATE casebook SET title=?, summary=?, updated_at=datetime('now') "
                    "WHERE case_id=?", (title.strip() or "Untitled case", summary.strip(), case_id))
        con.commit()
    finally:
        con.close()


def set_status(case_id: int, status: str) -> None:
    status = status if status in ("open", "complete") else "open"
    con = _rw()
    try:
        con.execute("UPDATE casebook SET status=?, updated_at=datetime('now') WHERE case_id=?",
                    (status, case_id))
        con.commit()
    finally:
        con.close()


def delete(case_id: int) -> None:
    con = _rw()
    try:
        con.execute("DELETE FROM casebook_item WHERE case_id=?", (case_id,))
        con.execute("DELETE FROM casebook WHERE case_id=?", (case_id,))
        con.commit()
    finally:
        con.close()


# -- items --------------------------------------------------------------------

def add_item(case_id: int, kind: str, ref_id: str | None, label: str,
             note: str = "") -> tuple[int, bool]:
    """Add an item. For non-note kinds, dedupe on (case, kind, ref_id).
    Returns (item_id, created)."""
    con = _rw()
    try:
        if kind != "note" and ref_id is not None:
            ex = con.execute("SELECT item_id FROM casebook_item "
                             "WHERE case_id=? AND kind=? AND ref_id=?",
                             (case_id, kind, str(ref_id))).fetchone()
            if ex:
                _touch(con, case_id)
                con.commit()
                return int(ex[0]), False
        cur = con.execute(
            "INSERT INTO casebook_item (case_id, kind, ref_id, label, note, position) "
            "VALUES (?,?,?,?,?, (SELECT COALESCE(MAX(position),0)+1 FROM casebook_item WHERE case_id=?))",
            (case_id, kind, (str(ref_id) if ref_id is not None else None),
             label.strip()[:300], note.strip(), case_id))
        _touch(con, case_id)
        con.commit()
        return int(cur.lastrowid), True
    finally:
        con.close()


def remove_item(case_id: int, item_id: int) -> None:
    con = _rw()
    try:
        con.execute("DELETE FROM casebook_item WHERE item_id=? AND case_id=?", (item_id, case_id))
        _touch(con, case_id)
        con.commit()
    finally:
        con.close()


def update_note(case_id: int, item_id: int, note: str) -> None:
    con = _rw()
    try:
        con.execute("UPDATE casebook_item SET note=? WHERE item_id=? AND case_id=?",
                    (note.strip(), item_id, case_id))
        _touch(con, case_id)
        con.commit()
    finally:
        con.close()


def move(case_id: int, item_id: int, direction: str) -> None:
    """Swap an item with its neighbour of the same kind (reorder within a group)."""
    con = _rw()
    try:
        row = con.execute("SELECT kind, position FROM casebook_item WHERE item_id=? AND case_id=?",
                          (item_id, case_id)).fetchone()
        if not row:
            return
        kind, pos = row[0], row[1]
        if direction == "up":
            nb = con.execute("SELECT item_id, position FROM casebook_item WHERE case_id=? AND kind=? "
                             "AND position<? ORDER BY position DESC LIMIT 1", (case_id, kind, pos)).fetchone()
        else:
            nb = con.execute("SELECT item_id, position FROM casebook_item WHERE case_id=? AND kind=? "
                             "AND position>? ORDER BY position ASC LIMIT 1", (case_id, kind, pos)).fetchone()
        if not nb:
            return
        con.execute("UPDATE casebook_item SET position=? WHERE item_id=?", (nb[1], item_id))
        con.execute("UPDATE casebook_item SET position=? WHERE item_id=?", (pos, nb[0]))
        _touch(con, case_id)
        con.commit()
    finally:
        con.close()
