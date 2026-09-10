"""SQLite storage for sessions. One row per recording or upload."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id                 TEXT PRIMARY KEY,
    title              TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    source             TEXT NOT NULL,          -- 'live' | 'upload'
    device_name        TEXT,
    source_filename    TEXT,
    channels           INTEGER DEFAULT 1,
    duration_s         REAL,
    status             TEXT NOT NULL,          -- recording|processing|done|error
    stage              TEXT,
    progress           REAL DEFAULT 0,
    error              TEXT,
    speaker_count      INTEGER,
    speaker_names_json TEXT,
    skill_name         TEXT,
    utterances_json    TEXT,
    summary_md         TEXT,
    drive_path         TEXT
);
"""


def _conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.ensure_dirs()
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _local.conn = conn
    return conn


def init() -> None:
    _conn()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_session(
    *,
    title: str,
    source: str,
    skill_name: str | None = None,
    device_name: str | None = None,
    source_filename: str | None = None,
    status: str = "processing",
) -> str:
    sid = uuid.uuid4().hex[:12]
    _conn().execute(
        """INSERT INTO sessions
           (id, title, created_at, source, device_name, source_filename,
            skill_name, status, stage, progress)
           VALUES (?,?,?,?,?,?,?,?,?,0)""",
        (sid, title, _now(), source, device_name, source_filename, skill_name,
         status, "queued"),
    )
    _conn().commit()
    return sid


def update(sid: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    _conn().execute(
        f"UPDATE sessions SET {cols} WHERE id = ?", (*fields.values(), sid)
    )
    _conn().commit()


def set_stage(sid: str, stage: str, progress: float) -> None:
    update(sid, stage=stage, progress=progress)


def fail(sid: str, message: str) -> None:
    update(sid, status="error", error=message, stage="failed")


def get(sid: str) -> dict[str, Any] | None:
    row = _conn().execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    return _hydrate(row) if row else None


def list_sessions(limit: int = 200) -> list[dict[str, Any]]:
    rows = _conn().execute(
        """SELECT id, title, created_at, source, duration_s, status, stage,
                  progress, error, speaker_count, skill_name, drive_path
           FROM sessions ORDER BY created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete(sid: str) -> None:
    _conn().execute("DELETE FROM sessions WHERE id = ?", (sid,))
    _conn().commit()


def _hydrate(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key, target in (("utterances_json", "utterances"),
                        ("speaker_names_json", "speaker_names")):
        raw = d.pop(key, None)
        d[target] = json.loads(raw) if raw else ([] if target == "utterances" else {})
    return d


def save_utterances(sid: str, utterances: list[dict[str, Any]]) -> None:
    speakers = sorted({u["speaker"] for u in utterances})
    update(
        sid,
        utterances_json=json.dumps(utterances, ensure_ascii=False),
        speaker_count=len(speakers),
    )


def save_speaker_names(sid: str, names: dict[str, str]) -> None:
    update(sid, speaker_names_json=json.dumps(names, ensure_ascii=False))
