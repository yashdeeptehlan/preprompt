"""SQLite storage for PrePrompt — persists to ~/.preprompt/history.db."""

import os
import uuid
import json
import socket
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DB_PATH = Path.home() / ".preprompt" / "history.db"
_conn: Optional[sqlite3.Connection] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s) -> datetime:
    if isinstance(s, datetime):
        return s
    return datetime.fromisoformat(s)


# ── Schema helper ─────────────────────────────────────────────────────────────

def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't exist. Works on any connection."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prompt_history (
            id               TEXT PRIMARY KEY,
            timestamp        TEXT,
            original_prompt  TEXT,
            optimized_prompt TEXT,
            classifier_score INTEGER,
            was_intercepted  INTEGER,
            turn_number      INTEGER,
            session_id       TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stack_memory (
            id           TEXT PRIMARY KEY,
            updated_at   TEXT,
            key          TEXT UNIQUE,
            value        TEXT,
            confidence   REAL,
            source_count INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id   TEXT PRIMARY KEY,
            started_at   TEXT,
            last_seen_at TEXT,
            hostname     TEXT,
            pid          INTEGER
        )
    """)
    conn.commit()


# ── Connection factory functions ──────────────────────────────────────────────

def _get_connection() -> sqlite3.Connection:
    """Return the long-lived write connection for this process (MCP server)."""
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(_conn)
    return _conn


def get_read_connection() -> sqlite3.Connection:
    """Fresh read connection — safe alongside a running MCP server (WAL mode)."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_write_connection() -> sqlite3.Connection:
    """Fresh write connection for hook subprocess. Caller must close it."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _ensure_schema(conn)
    return conn


# ── Session identity ──────────────────────────────────────────────────────────

def get_or_create_session() -> str:
    """Return a stable session_id for this machine+process.

    Strategy: one active session per hostname per calendar day.
    Restarting the server mid-day continues the same session; a new day
    always gets a fresh session.
    """
    hostname = socket.gethostname()
    today = datetime.now(timezone.utc).date().isoformat()
    session_key = f"{hostname}-{today}"

    conn = _get_connection()
    existing = conn.execute(
        "SELECT session_id FROM sessions WHERE session_id = ?",
        [session_key],
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE session_id = ?",
            [_now(), session_key],
        )
    else:
        conn.execute(
            "INSERT INTO sessions (session_id, started_at, last_seen_at, hostname, pid) "
            "VALUES (?, ?, ?, ?, ?)",
            [session_key, _now(), _now(), hostname, os.getpid()],
        )
    conn.commit()
    return session_key


# ── Prompt history ────────────────────────────────────────────────────────────

def save_prompt_event(
    original_prompt: str,
    optimized_prompt: str,
    classifier_score: int,
    was_intercepted: bool,
    turn_number: int,
    session_id: str,
) -> str:
    """Insert a prompt event and return its generated id."""
    event_id = str(uuid.uuid4())
    conn = _get_connection()
    conn.execute("""
        INSERT INTO prompt_history
            (id, timestamp, original_prompt, optimized_prompt,
             classifier_score, was_intercepted, turn_number, session_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        event_id,
        _now(),
        original_prompt,
        optimized_prompt,
        classifier_score,
        1 if was_intercepted else 0,
        turn_number,
        session_id,
    ])
    conn.commit()
    return event_id


def get_recent_history(session_id: str, limit: int = 20) -> list[dict]:
    """Return the most recent *limit* events for *session_id*."""
    conn = _get_connection()
    rows = conn.execute("""
        SELECT id, timestamp, original_prompt, optimized_prompt,
               classifier_score, was_intercepted, turn_number, session_id
        FROM prompt_history
        WHERE session_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, [session_id, limit]).fetchall()
    cols = ["id", "timestamp", "original_prompt", "optimized_prompt",
            "classifier_score", "was_intercepted", "turn_number", "session_id"]
    return [_coerce_row(dict(zip(cols, row))) for row in rows]


def get_all_history(limit: int = 20, intercepted_only: bool = False) -> list[dict]:
    """Return most recent events across all sessions. Uses a fresh read connection."""
    where = "WHERE was_intercepted = 1" if intercepted_only else ""
    conn = get_read_connection()
    try:
        rows = conn.execute(f"""
            SELECT id, timestamp, original_prompt, optimized_prompt,
                   classifier_score, was_intercepted, turn_number, session_id
            FROM prompt_history
            {where}
            ORDER BY timestamp DESC
            LIMIT ?
        """, [limit]).fetchall()
        cols = ["id", "timestamp", "original_prompt", "optimized_prompt",
                "classifier_score", "was_intercepted", "turn_number", "session_id"]
        return [_coerce_row(dict(zip(cols, row))) for row in rows]
    finally:
        conn.close()


def _coerce_row(row: dict) -> dict:
    """Normalise types coming back from SQLite."""
    if "timestamp" in row and isinstance(row["timestamp"], str):
        row["timestamp"] = _parse_dt(row["timestamp"])
    if "was_intercepted" in row:
        row["was_intercepted"] = bool(row["was_intercepted"])
    return row


# ── Stack memory ──────────────────────────────────────────────────────────────

def upsert_stack_memory(key: str, value: str, confidence: float) -> None:
    """Upsert a stack memory entry with compounding confidence.

    - First occurrence: store confidence as-is.
    - Same key + same value again: new_confidence = min(0.99, existing + 0.02)
    - Same key + different value: reset confidence to 0.6, reset source_count to 1.
    """
    conn = _get_connection()
    existing = conn.execute(
        "SELECT id, value, confidence, source_count FROM stack_memory WHERE key = ?",
        [key],
    ).fetchone()

    if existing:
        entry_id, existing_value, existing_confidence, source_count = existing
        if existing_value == value:
            new_confidence = min(0.99, existing_confidence + 0.02)
            conn.execute("""
                UPDATE stack_memory
                SET updated_at = ?, confidence = ?, source_count = ?
                WHERE id = ?
            """, [_now(), new_confidence, source_count + 1, entry_id])
        else:
            conn.execute("""
                UPDATE stack_memory
                SET updated_at = ?, value = ?, confidence = 0.6, source_count = 1
                WHERE id = ?
            """, [_now(), value, entry_id])
    else:
        conn.execute("""
            INSERT INTO stack_memory (id, updated_at, key, value, confidence, source_count)
            VALUES (?, ?, ?, ?, ?, 1)
        """, [str(uuid.uuid4()), _now(), key, value, confidence])
    conn.commit()


def get_stack_memory() -> dict[str, str]:
    """Return {key: value} for all entries with confidence >= 0.6."""
    conn = _get_connection()
    rows = conn.execute("""
        SELECT key, value FROM stack_memory
        WHERE confidence >= 0.6
        ORDER BY confidence DESC
    """).fetchall()
    return {row[0]: row[1] for row in rows}


def get_full_stack_memory() -> list[dict]:
    """Return all entries including confidence and source_count.
    Uses a fresh read connection — safe alongside a running MCP server.
    """
    conn = get_read_connection()
    try:
        rows = conn.execute("""
            SELECT key, value, confidence, source_count, updated_at
            FROM stack_memory
            ORDER BY confidence DESC
        """).fetchall()
    finally:
        conn.close()
    cols = ["key", "value", "confidence", "source_count", "updated_at"]
    result = []
    for row in rows:
        d = dict(zip(cols, row))
        if isinstance(d.get("updated_at"), str):
            d["updated_at"] = _parse_dt(d["updated_at"])
        result.append(d)
    return result


# ── Sidecar flush ──────────────────────────────────────────────────────────────

def flush_pending_hook_events() -> int:
    """Read all JSON sidecar files from ~/.preprompt/pending/, insert into
    prompt_history, delete each file. Returns count of events flushed.

    Called by the MCP server at the start of optimize_prompt() so hook events
    are persisted without the hook ever touching the DB directly.
    Also runs update_memory_from_prompt() on each flushed event so Claude Code
    sessions contribute to stack memory.
    """
    pending_dir = Path.home() / ".preprompt" / "pending"
    if not pending_dir.exists():
        return 0

    conn = _get_connection()
    flushed = 0
    for sidecar_path in pending_dir.glob("*.json"):
        try:
            data = json.loads(sidecar_path.read_text())
            conn.execute("""
                INSERT OR IGNORE INTO prompt_history
                    (id, timestamp, original_prompt, optimized_prompt,
                     classifier_score, was_intercepted, turn_number, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                str(uuid.uuid4()),
                datetime.fromtimestamp(data["timestamp"], tz=timezone.utc).isoformat(),
                data["original_prompt"],
                data["optimized_prompt"],
                data["classifier_score"],
                1 if data["was_intercepted"] else 0,
                data["turn_number"],
                "hook-session",
            ])
            conn.commit()
            sidecar_path.unlink()
            flushed += 1
            try:
                from mcp_server.extractor import update_memory_from_prompt
                update_memory_from_prompt(data["original_prompt"], [])
            except Exception:
                pass
        except Exception:
            pass
    return flushed
