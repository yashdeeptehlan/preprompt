"""DuckDB storage for PromptForge — persists to ~/.promptforge/history.db."""

import os
import uuid
import json
import socket
import duckdb
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DB_PATH = Path.home() / ".promptforge" / "history.db"
_conn: Optional[duckdb.DuckDBPyConnection] = None


# ── Schema helper ─────────────────────────────────────────────────────────────

def _ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create all tables if they don't exist. Works on any write connection."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prompt_history (
            id               VARCHAR PRIMARY KEY,
            timestamp        TIMESTAMP,
            original_prompt  TEXT,
            optimized_prompt TEXT,
            classifier_score INTEGER,
            was_intercepted  BOOLEAN,
            turn_number      INTEGER,
            session_id       VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stack_memory (
            id           VARCHAR PRIMARY KEY,
            updated_at   TIMESTAMP,
            key          VARCHAR UNIQUE,
            value        TEXT,
            confidence   DOUBLE,
            source_count INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id   VARCHAR PRIMARY KEY,
            started_at   TIMESTAMP,
            last_seen_at TIMESTAMP,
            hostname     VARCHAR,
            pid          INTEGER
        )
    """)


# ── Connection factory functions ──────────────────────────────────────────────

def _get_connection() -> duckdb.DuckDBPyConnection:
    """Return the long-lived write connection for this process (MCP server)."""
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = duckdb.connect(str(_DB_PATH))
        _conn.execute("PRAGMA enable_checkpoint_on_shutdown")
        _ensure_schema(_conn)
    return _conn


def get_write_connection() -> duckdb.DuckDBPyConnection:
    """Return a fresh short-lived write connection.

    Caller MUST close it immediately after use.
    Used by the hook to avoid lock conflicts with the MCP server.
    """
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(_DB_PATH))
    _ensure_schema(conn)
    return conn


def get_read_connection() -> duckdb.DuckDBPyConnection:
    """Return a read-only connection. Safe to use alongside a running MCP server."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(_DB_PATH), read_only=True)


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
            [datetime.now(timezone.utc), session_key],
        )
    else:
        conn.execute(
            "INSERT INTO sessions (session_id, started_at, last_seen_at, hostname, pid) "
            "VALUES (?, ?, ?, ?, ?)",
            [session_key, datetime.now(timezone.utc), datetime.now(timezone.utc),
             hostname, os.getpid()],
        )
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
        datetime.now(timezone.utc),
        original_prompt,
        optimized_prompt,
        classifier_score,
        was_intercepted,
        turn_number,
        session_id,
    ])
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
    return [dict(zip(cols, row)) for row in rows]


def get_all_history(limit: int = 20, intercepted_only: bool = False) -> list[dict]:
    """Return most recent events across all sessions.

    CLI callers run in a separate process where _conn is None — a fresh
    read-only connection is opened and closed.  In-process callers (MCP server,
    tests) already hold _conn so we reuse it to avoid the mixed-mode conflict.
    """
    where = "WHERE was_intercepted = TRUE" if intercepted_only else ""
    owned = _conn is None
    conn = duckdb.connect(str(_DB_PATH), read_only=True) if owned else _conn
    try:
        rows = conn.execute(f"""
            SELECT id, timestamp, original_prompt, optimized_prompt,
                   classifier_score, was_intercepted, turn_number, session_id
            FROM prompt_history
            {where}
            ORDER BY timestamp DESC
            LIMIT ?
        """, [limit]).fetchall()
    finally:
        if owned:
            conn.close()
    cols = ["id", "timestamp", "original_prompt", "optimized_prompt",
            "classifier_score", "was_intercepted", "turn_number", "session_id"]
    return [dict(zip(cols, row)) for row in rows]


# ── Stack memory ──────────────────────────────────────────────────────────────

def upsert_stack_memory(key: str, value: str, confidence: float) -> None:
    """Upsert a stack memory entry with compounding confidence.

    - First occurrence: store confidence as-is.
    - Same key + same value again: new_confidence = min(0.99, existing + 0.02)
    - Same key + different value: reset confidence to 0.6, reset source_count to 1.

    get_stack_memory() filters at >= 0.6, meaning "seen at least once with no
    contradictions". Confidence compounds with each confirming observation.
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
            """, [datetime.now(timezone.utc), new_confidence, source_count + 1, entry_id])
        else:
            conn.execute("""
                UPDATE stack_memory
                SET updated_at = ?, value = ?, confidence = 0.6, source_count = 1
                WHERE id = ?
            """, [datetime.now(timezone.utc), value, entry_id])
    else:
        conn.execute("""
            INSERT INTO stack_memory (id, updated_at, key, value, confidence, source_count)
            VALUES (?, ?, ?, ?, ?, 1)
        """, [str(uuid.uuid4()), datetime.now(timezone.utc), key, value, confidence])


def get_stack_memory() -> dict[str, str]:
    """Return {key: value} for all entries with confidence >= 0.6.

    Threshold of 0.6 means "seen at least once with no contradictions".
    Confidence compounds with repeated confirming observations.
    """
    conn = _get_connection()
    rows = conn.execute("""
        SELECT key, value FROM stack_memory
        WHERE confidence >= 0.6
        ORDER BY confidence DESC
    """).fetchall()
    return {row[0]: row[1] for row in rows}


def get_full_stack_memory() -> list[dict]:
    """Return all entries including confidence and source_count.

    CLI callers run in a separate process where _conn is None — a fresh
    read-only connection is opened and closed.  In-process callers reuse _conn.
    """
    owned = _conn is None
    conn = duckdb.connect(str(_DB_PATH), read_only=True) if owned else _conn
    try:
        rows = conn.execute("""
            SELECT key, value, confidence, source_count, updated_at
            FROM stack_memory
            ORDER BY confidence DESC
        """).fetchall()
    finally:
        if owned:
            conn.close()
    cols = ["key", "value", "confidence", "source_count", "updated_at"]
    return [dict(zip(cols, row)) for row in rows]


# ── Sidecar flush ──────────────────────────────────────────────────────────────

def flush_pending_hook_events() -> int:
    """Read all JSON sidecar files from ~/.promptforge/pending/, insert into
    prompt_history, delete each file. Returns count of events flushed.

    Called by the MCP server at the start of optimize_prompt() so hook events
    are persisted without the hook ever touching the DB directly.
    """
    pending_dir = Path.home() / ".promptforge" / "pending"
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
                datetime.fromtimestamp(data["timestamp"], tz=timezone.utc),
                data["original_prompt"],
                data["optimized_prompt"],
                data["classifier_score"],
                data["was_intercepted"],
                data["turn_number"],
                "hook-session",
            ])
            sidecar_path.unlink()
            flushed += 1
        except Exception:
            pass
    return flushed
