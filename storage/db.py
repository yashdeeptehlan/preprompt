"""SQLite storage for PrePrompt — persists to ~/.preprompt/history.db."""

import logging
import os
import uuid
import json
import socket
import sqlite3
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("preprompt.storage")

_DB_PATH = Path.home() / ".preprompt" / "history.db"
_session_lock = threading.Lock()
_write_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _chmod_user_only(path: Path) -> None:
    """Best-effort chmod 0o600 — silently no-op on Windows / unsupported FS."""
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


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
            session_id       TEXT,
            user_kept        INTEGER DEFAULT NULL
        )
    """)
    try:
        conn.execute("ALTER TABLE prompt_history ADD COLUMN user_kept INTEGER DEFAULT NULL")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE prompt_history ADD COLUMN route TEXT DEFAULT 'enrich'")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
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

def _prepare_db_dir() -> bool:
    """Create the parent dir and return True if the DB file is being created now."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return not _DB_PATH.exists()


def _harden_db_file() -> None:
    """Lock down history.db so other UNIX users can't read it (audit L-13)."""
    if _DB_PATH.exists():
        _chmod_user_only(_DB_PATH)


def _get_connection() -> sqlite3.Connection:
    """Return the long-lived write connection for this process (MCP server)."""
    global _conn
    if _conn is None:
        is_new = _prepare_db_dir()
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(_conn)
        if is_new:
            _harden_db_file()
    return _conn


def get_read_connection() -> sqlite3.Connection:
    """Fresh read connection — safe alongside a running MCP server (WAL mode)."""
    _prepare_db_dir()
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_write_connection() -> sqlite3.Connection:
    """Fresh write connection for hook subprocess. Caller must close it."""
    is_new = _prepare_db_dir()
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _ensure_schema(conn)
    if is_new:
        _harden_db_file()
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

    with _session_lock:
        conn = _get_connection()
        with _write_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO sessions (session_id, started_at, last_seen_at, hostname, pid) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [session_key, _now(), _now(), hostname, os.getpid()],
                )
                conn.execute(
                    "UPDATE sessions SET last_seen_at = ?, pid = ? WHERE session_id = ?",
                    [_now(), os.getpid(), session_key],
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    return session_key


# ── Prompt history ────────────────────────────────────────────────────────────

def save_prompt_event(
    original_prompt: str,
    optimized_prompt: str,
    classifier_score: int,
    was_intercepted: bool,
    turn_number: int,
    session_id: str,
    route: str = "enrich",
) -> str:
    """Insert a prompt event and return its generated id.

    Audit M-6: serialise writes through _write_lock + BEGIN IMMEDIATE so the
    shared process-wide connection (used by MCP server and dashboard flush)
    can't interleave statements between INSERT and COMMIT.
    """
    event_id = str(uuid.uuid4())
    conn = _get_connection()
    with _write_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("""
                INSERT INTO prompt_history
                    (id, timestamp, original_prompt, optimized_prompt,
                     classifier_score, was_intercepted, turn_number, session_id, route)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                event_id,
                _now(),
                original_prompt,
                optimized_prompt,
                classifier_score,
                1 if was_intercepted else 0,
                turn_number,
                session_id,
                route,
            ])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
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


def get_all_history(limit: int = 100, intercepted_only: bool = False) -> list[dict]:
    """Return most recent events across all sessions. Uses a fresh read connection."""
    where = "WHERE was_intercepted = 1" if intercepted_only else ""
    conn = get_read_connection()
    try:
        rows = conn.execute(f"""
            SELECT id, timestamp, original_prompt, optimized_prompt,
                   classifier_score, was_intercepted, turn_number, session_id, route,
                   user_kept
            FROM prompt_history
            {where}
            ORDER BY timestamp DESC
            LIMIT ?
        """, [limit]).fetchall()
        cols = ["id", "timestamp", "original_prompt", "optimized_prompt",
                "classifier_score", "was_intercepted", "turn_number", "session_id", "route",
                "user_kept"]
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
    with _write_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT id, value, confidence, source_count FROM stack_memory WHERE key = ?",
                [key],
            ).fetchone()

            if existing:
                entry_id, existing_value, existing_confidence, source_count = existing
                if existing_value == value:
                    new_confidence = min(0.99, existing_confidence + 0.03)
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
        except Exception:
            conn.rollback()
            raise


def get_stack_memory_with_confidence() -> list[dict]:
    """Return all stack memory entries with confidence. Uses a fresh read connection."""
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


def get_route_stats() -> dict:
    """Return count of prompts by route (pass/enrich/clarify). Uses a fresh read connection."""
    conn = get_read_connection()
    try:
        rows = conn.execute("""
            SELECT COALESCE(route, 'enrich') as route, COUNT(*) as count
            FROM prompt_history
            GROUP BY COALESCE(route, 'enrich')
        """).fetchall()
        stats = {"pass": 0, "enrich": 0, "clarify": 0}
        for route, count in rows:
            if route in stats:
                stats[route] = count
            else:
                stats["enrich"] += count
        stats["total"] = sum(stats.values())
        return stats
    finally:
        conn.close()


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


# ── User feedback ─────────────────────────────────────────────────────────────

def record_user_feedback(event_id: str, kept: bool) -> None:
    """Record whether user kept (1) or rejected (0) the optimization."""
    conn = _get_connection()
    with _write_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "UPDATE prompt_history SET user_kept = ? WHERE id = ?",
                [1 if kept else 0, event_id],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def get_d30_retention() -> list[dict]:
    """Compute D30 retention cohorts from the local prompt_history table.

    A "user" in single-machine local mode is proxied by ``session_id`` (one
    session per hostname per calendar day in the current scheme). A session
    is "retained at D30" if it has any prompt 28–32 days after its first.

    Returns one row per ISO week of first activity, newest cohort first::

        [
          {"cohort_week": "2026-22", "cohort_size": 14, "retained_d30": 6,
           "retention_pct": 42.9},
          ...
        ]

    In production this same shape will be computed server-side on
    ``usage_events`` using the canonical ``user_id`` — see W3 spec.
    """
    conn = get_read_connection()
    try:
        rows = conn.execute("""
            WITH first_seen AS (
                SELECT session_id, MIN(timestamp) AS first_ts
                FROM prompt_history
                WHERE session_id IS NOT NULL
                GROUP BY session_id
            ),
            retained_sessions AS (
                SELECT DISTINCT fs.session_id
                FROM first_seen fs
                JOIN prompt_history ph ON ph.session_id = fs.session_id
                WHERE (julianday(ph.timestamp) - julianday(fs.first_ts))
                      BETWEEN 28 AND 32
            )
            SELECT
                strftime('%Y-%W', fs.first_ts)  AS cohort_week,
                COUNT(DISTINCT fs.session_id)   AS cohort_size,
                COUNT(DISTINCT r.session_id)    AS retained_d30
            FROM first_seen fs
            LEFT JOIN retained_sessions r ON r.session_id = fs.session_id
            GROUP BY cohort_week
            ORDER BY cohort_week DESC
        """).fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for cohort_week, cohort_size, retained_d30 in rows:
        pct = round(retained_d30 / cohort_size * 100, 1) if cohort_size else 0.0
        out.append({
            "cohort_week": cohort_week,
            "cohort_size": cohort_size,
            "retained_d30": retained_d30,
            "retention_pct": pct,
        })
    return out


def rate_last_intercepted(kept: bool) -> bool:
    """Mark the most recent intercepted-but-unrated prompt as kept (True) or rejected (False).

    Returns True if a row was updated, False if no eligible row was found.
    """
    conn = _get_connection()
    with _write_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("""
                SELECT id FROM prompt_history
                WHERE was_intercepted = 1 AND user_kept IS NULL
                ORDER BY timestamp DESC
                LIMIT 1
            """).fetchone()
            if row is None:
                conn.rollback()
                return False
            conn.execute(
                "UPDATE prompt_history SET user_kept = ? WHERE id = ?",
                [1 if kept else 0, row[0]],
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise


def get_feedback_stats() -> dict:
    """Return accept/reject stats for intercepted prompts."""
    conn = get_read_connection()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*) as total_intercepted,
                SUM(CASE WHEN user_kept = 1 THEN 1 ELSE 0 END) as kept,
                SUM(CASE WHEN user_kept = 0 THEN 1 ELSE 0 END) as rejected,
                SUM(CASE WHEN user_kept IS NULL THEN 1 ELSE 0 END) as no_feedback
            FROM prompt_history
            WHERE was_intercepted = 1
        """).fetchone()
        total, kept, rejected, no_feedback = row
        kept = kept or 0
        rejected = rejected or 0
        rated = kept + rejected
        accept_rate = round(kept / rated * 100) if rated > 0 else None
        return {
            "total_intercepted": total or 0,
            "kept": kept,
            "rejected": rejected,
            "no_feedback": no_feedback or 0,
            "accept_rate": accept_rate,
        }
    finally:
        conn.close()


# ── Sidecar flush ──────────────────────────────────────────────────────────────

def flush_pending_hook_events() -> dict:
    """Read all JSON sidecar files from ~/.preprompt/pending/, insert into
    prompt_history, delete each file.

    Audit M-7: malformed sidecars used to be silently dropped — production
    losing analytics data the moment a hook wrote a bad file. Failures are now
    logged and the file is moved to ``pending/failed/`` for postmortem rather
    than discarded.

    Returns {"count": N, "prompts": [{"prompt": str, "history": list}, ...]}.
    """
    pending_dir = Path.home() / ".preprompt" / "pending"
    if not pending_dir.exists():
        return {"count": 0, "prompts": []}

    failed_dir = pending_dir / "failed"
    conn = _get_connection()
    flushed = 0
    prompts = []
    for sidecar_path in pending_dir.glob("*.json"):
        try:
            data = json.loads(sidecar_path.read_text())
            with _write_lock:
                conn.execute("BEGIN IMMEDIATE")
                try:
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
                except Exception:
                    conn.rollback()
                    raise
            sidecar_path.unlink()
            flushed += 1
            prompts.append({"prompt": data["original_prompt"], "history": []})
        except Exception:
            logger.warning("failed to flush sidecar %s", sidecar_path.name, exc_info=True)
            try:
                failed_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(sidecar_path), str(failed_dir / sidecar_path.name))
            except Exception:
                logger.exception("quarantining sidecar failed")
    return {"count": flushed, "prompts": prompts}
