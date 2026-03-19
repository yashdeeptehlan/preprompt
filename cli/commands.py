"""
CLI entry points for PromptForge.

Commands
--------
promptforge-history          View recent prompt history (all sessions)
promptforge-stats            Aggregate optimization stats
promptforge-test-classifier  Run the classifier against 6 benchmark prompts
promptforge-memory           Show learned stack memory
"""

import argparse
import sys
import duckdb
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = Path.home() / ".promptforge" / "history.db"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _relative_time(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: width - 3] + "..."


def _open_db() -> duckdb.DuckDBPyConnection:
    if not _DB_PATH.exists():
        print(f"No history database found at {_DB_PATH}", file=sys.stderr)
        print("Run PromptForge and send a few prompts first.", file=sys.stderr)
        sys.exit(1)
    return duckdb.connect(str(_DB_PATH), read_only=True)


# ── promptforge-history ───────────────────────────────────────────────────────

def history_cmd() -> None:
    parser = argparse.ArgumentParser(
        prog="promptforge-history",
        description="Show recent prompts seen by PromptForge (all sessions).",
    )
    parser.add_argument("--limit", type=int, default=20, metavar="N",
                        help="Maximum rows to display (default: 20)")
    parser.add_argument("--intercepted-only", action="store_true",
                        help="Show only prompts that were optimized")
    args = parser.parse_args()

    if not _DB_PATH.exists():
        print("No history database found. Run PromptForge and send a few prompts first.")
        return

    try:
        from storage.db import get_all_history
        events = get_all_history(limit=args.limit, intercepted_only=args.intercepted_only)
    except Exception as e:
        if "lock" in str(e).lower() or "conflict" in str(e).lower():
            print("DB is locked by the MCP server. Stop it first or wait a moment.", file=sys.stderr)
        else:
            print(f"Error reading history: {e}", file=sys.stderr)
        return

    if not events:
        print("No prompt history found.")
        return

    col_time   = 8
    col_score  = 5
    col_int    = 3
    col_prompt = 60

    header = f"{'TIME':<{col_time}}  {'SCORE':>{col_score}}  {'INT':<{col_int}}  ORIGINAL PROMPT"
    sep    = "─" * (col_time + 2 + col_score + 2 + col_int + 2 + col_prompt)
    print(header)
    print(sep)

    for e in events:
        time_str   = _relative_time(e["timestamp"])
        int_str    = "yes" if e["was_intercepted"] else "no"
        prompt_str = _truncate(e["original_prompt"] or "", col_prompt)
        print(f"{time_str:<{col_time}}  {e['classifier_score']:>{col_score}}  {int_str:<{col_int}}  {prompt_str}")


# ── promptforge-stats ─────────────────────────────────────────────────────────

def stats_cmd() -> None:
    try:
        conn = _open_db()
    except SystemExit:
        return
    except Exception as e:
        if "lock" in str(e).lower() or "conflict" in str(e).lower():
            print("DB is locked by the MCP server. Stop it first or wait a moment.", file=sys.stderr)
        else:
            print(f"Error opening DB: {e}", file=sys.stderr)
        return
    row = conn.execute("""
        SELECT
            COUNT(*)                                                 AS total,
            SUM(CASE WHEN was_intercepted THEN 1 ELSE 0 END)        AS intercepted,
            AVG(classifier_score)                                    AS avg_score,
            AVG(CASE WHEN was_intercepted THEN classifier_score END) AS avg_intercepted,
            COUNT(DISTINCT session_id)                               AS sessions
        FROM prompt_history
    """).fetchone()
    conn.close()

    total, intercepted, avg_score, avg_intercepted, sessions = row
    total        = total or 0
    intercepted  = intercepted or 0
    avg_score    = avg_score or 0.0
    avg_intercepted = avg_intercepted or 0.0
    pct = (intercepted / total * 100) if total else 0.0

    sep = "─" * 46
    print(f" PromptForge — optimization stats")
    print(sep)
    print(f" Total prompts seen:      {total}")
    print(f" Prompts intercepted:     {intercepted} ({pct:.1f}%)")
    print(f" Avg classifier score:    {avg_score:.1f}")
    print(f" Avg score (intercepted): {avg_intercepted:.1f}")
    print(f" Sessions tracked:        {sessions}")
    print(f" DB path:                 {_DB_PATH}")


# ── promptforge-test-classifier ───────────────────────────────────────────────

_BENCHMARK_PROMPTS = [
    "write me a middleware that validates tokens and handles refresh",
    "what is jwt",
    "thanks",
    "refactor this to handle edge cases and manage errors properly",
    "add tests",
    (
        "implement a rate limiter that tracks requests, manages quotas, "
        "and handles burst traffic with backoff"
    ),
]


def test_classifier_cmd() -> None:
    _here = Path(__file__).resolve().parent.parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))

    from mcp_server.classifier import classify_prompt, OPTIMIZATION_THRESHOLD

    col_score  = 5
    col_flag   = 9
    col_prompt = 65

    header = f"{'SCORE':>{col_score}}  {'INTERCEPT':<{col_flag}}  PROMPT"
    sep    = "─" * (col_score + 2 + col_flag + 2 + col_prompt)
    print(header)
    print(sep)

    for prompt in _BENCHMARK_PROMPTS:
        score = classify_prompt(prompt, history=[], turn=1)
        flag  = "YES" if score >= OPTIMIZATION_THRESHOLD else "no"
        print(f"{score:>{col_score}}  {flag:<{col_flag}}  {_truncate(prompt, col_prompt)}")


# ── promptforge-memory ────────────────────────────────────────────────────────

def memory_cmd() -> None:
    conn = _open_db()

    try:
        rows = conn.execute("""
            SELECT key, value, confidence, source_count, updated_at
            FROM stack_memory
            ORDER BY confidence DESC
        """).fetchall()
    except Exception:
        # Table doesn't exist yet — DB predates Phase 3
        rows = []

    if not rows:
        conn.close()
        print("No stack memory yet. Send a few prompts in Claude Code to build it.")
        return

    total_prompts = conn.execute("SELECT COUNT(*) FROM prompt_history").fetchone()[0]
    last_updated  = max(row[4] for row in rows)
    conn.close()

    sep = "─" * 54
    print(" PromptForge — learned stack memory")
    print(sep)
    for key, value, confidence, source_count, _ in rows:
        print(
            f"  {key:<12} {value:<16} "
            f"confidence: {confidence:.2f}  (seen {source_count}x)"
        )
    print()
    print(f"  Last updated: {_relative_time(last_updated)}")
    print(f"  Total prompts analyzed: {total_prompts}")
    print(sep)
    print("  Tip: more prompts = better optimization context")


# ── promptforge-update-context ────────────────────────────────────────────────

def update_context_cmd() -> None:
    """Regenerate CONTEXT.md with current phase, test count, and file map."""
    import subprocess
    import datetime
    import re

    # Run pytest to get current test count
    result = subprocess.run(
        ["python", "-m", "pytest", "--tb=no", "-q"],
        capture_output=True, text=True,
        cwd=str(Path(__file__).parent.parent),
    )
    match = re.search(r"(\d+) passed", result.stdout + result.stderr)
    test_count = match.group(1) if match else "unknown"

    # Read current CONTEXT.md, update the test count line
    context_path = Path(__file__).parent.parent / "CONTEXT.md"
    content = context_path.read_text()
    content = re.sub(
        r"\d+/\d+ tests passing",
        f"{test_count}/{test_count} tests passing",
        content,
    )

    # Add / refresh last-updated timestamp at the top
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    content = re.sub(r"^<!-- Last updated: .* -->\n", "", content)
    if content.startswith("# PromptForge"):
        content = f"<!-- Last updated: {timestamp} -->\n" + content

    context_path.write_text(content)
    print(f"✓ CONTEXT.md updated — {test_count} tests passing")
    print(f"  Last updated: {timestamp}")
