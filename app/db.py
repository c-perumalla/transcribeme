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

CREATE TABLE IF NOT EXISTS batches (
    id           TEXT PRIMARY KEY,
    folder       TEXT NOT NULL,
    options_json TEXT NOT NULL,
    origin       TEXT NOT NULL,                -- 'web' | 'cli'
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS batch_items (
    batch_id     TEXT NOT NULL,
    position     INTEGER NOT NULL,
    path         TEXT NOT NULL,
    rel_path     TEXT NOT NULL,
    duration_s   REAL,
    status       TEXT NOT NULL,     -- queued|running|done|skipped|error|cancelled
    session_id   TEXT,
    outputs_json TEXT,
    error        TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    PRIMARY KEY (batch_id, position)
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


def list_sessions(limit: int = 500) -> list[dict[str, Any]]:
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


# --- Batches ----------------------------------------------------------------

def create_batch(folder: str, options: dict[str, Any], origin: str,
                 items: list[dict[str, Any]]) -> str:
    bid = uuid.uuid4().hex[:12]
    conn = _conn()
    conn.execute(
        "INSERT INTO batches (id, folder, options_json, origin, created_at) "
        "VALUES (?,?,?,?,?)",
        (bid, folder, json.dumps(options), origin, _now()),
    )
    conn.executemany(
        """INSERT INTO batch_items
           (batch_id, position, path, rel_path, duration_s, status, outputs_json, error)
           VALUES (?,?,?,?,?,?,?,?)""",
        [
            (bid, i, item["path"], item["rel_path"], item.get("duration_s"),
             item["status"], json.dumps(item.get("outputs") or {}), item.get("error"))
            for i, item in enumerate(items)
        ],
    )
    conn.commit()
    return bid


def update_batch_item(bid: str, position: int, **fields: Any) -> None:
    if "outputs" in fields:
        fields["outputs_json"] = json.dumps(fields.pop("outputs"))
    for stamp in ("started_at", "finished_at"):
        if fields.get(stamp) is True:
            fields[stamp] = _now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    _conn().execute(
        f"UPDATE batch_items SET {cols} WHERE batch_id = ? AND position = ?",
        (*fields.values(), bid, position),
    )
    _conn().commit()


def finish_batch(bid: str) -> None:
    _conn().execute("UPDATE batches SET finished_at = ? WHERE id = ?", (_now(), bid))
    _conn().commit()


def cancel_queued(bid: str) -> int:
    cur = _conn().execute(
        "UPDATE batch_items SET status = 'cancelled' "
        "WHERE batch_id = ? AND status = 'queued'", (bid,),
    )
    _conn().commit()
    return cur.rowcount


def get_batch(bid: str) -> dict[str, Any] | None:
    row = _conn().execute("SELECT * FROM batches WHERE id = ?", (bid,)).fetchone()
    if not row:
        return None
    batch = dict(row)
    batch["options"] = json.loads(batch.pop("options_json"))
    # Live stage/progress come from the session each item spawned.
    rows = _conn().execute(
        """SELECT i.*, s.stage, s.progress
           FROM batch_items i LEFT JOIN sessions s ON s.id = i.session_id
           WHERE i.batch_id = ? ORDER BY i.position""",
        (bid,),
    ).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["outputs"] = json.loads(item.pop("outputs_json") or "{}")
        items.append(item)
    batch["items"] = items
    return batch


def get_batch_item(bid: str, position: int) -> dict[str, Any] | None:
    row = _conn().execute(
        "SELECT * FROM batch_items WHERE batch_id = ? AND position = ?",
        (bid, position),
    ).fetchone()
    return dict(row) if row else None


def latest_batch_id() -> str | None:
    row = _conn().execute(
        "SELECT id FROM batches WHERE origin = 'web' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def abandon_web_batches() -> None:
    """A server restart drops the in-memory job queue; reflect that honestly
    instead of leaving items 'queued' forever. CLI batches run in their own
    process and are left alone."""
    conn = _conn()
    web = "(SELECT id FROM batches WHERE origin = 'web')"
    conn.execute(f"UPDATE batch_items SET status = 'cancelled' "
                 f"WHERE status = 'queued' AND batch_id IN {web}")
    conn.execute(f"UPDATE batch_items SET status = 'error', "
                 f"error = 'Interrupted by a server restart' "
                 f"WHERE status = 'running' AND batch_id IN {web}")
    conn.commit()
