"""SQLite persistence for research reports."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

DB_PATH = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data")) / "xerien.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id          TEXT PRIMARY KEY,
    workspace   TEXT NOT NULL,
    question    TEXT NOT NULL,
    depth       TEXT NOT NULL,
    report      TEXT NOT NULL,
    trace       TEXT NOT NULL,
    model       TEXT,
    usage       TEXT,
    duration_ms INTEGER,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_workspace_created ON reports (workspace, created_at DESC);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect()) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)


def save(
    *, workspace: str, question: str, depth: str, report: str, trace: list[dict[str, Any]],
    model: str | None, usage: dict[str, Any] | None, duration_ms: int,
) -> str:
    rid = secrets.token_urlsafe(9)
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, workspace, question, depth, report, json.dumps(trace), model,
             json.dumps(usage or {}), duration_ms, time.time()),
        )
    return rid


def list_for(workspace: str, limit: int = 50) -> list[dict[str, Any]]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT id, question, depth, created_at FROM reports WHERE workspace = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (workspace, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get(rid: str) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (rid,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["trace"] = json.loads(out["trace"])
    out["usage"] = json.loads(out["usage"] or "{}")
    return out


def delete(rid: str, workspace: str) -> bool:
    with closing(_connect()) as conn, conn:
        cur = conn.execute("DELETE FROM reports WHERE id = ? AND workspace = ?", (rid, workspace))
    return cur.rowcount > 0
