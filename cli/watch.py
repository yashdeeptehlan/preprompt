"""
preprompt-watch — live feed of PrePrompt interceptions.

Run this in a second terminal while working in Claude Code or Cursor:
    preprompt-watch

Tails ~/.preprompt/activity.log and color-codes each event as it arrives.
Press Ctrl+C to exit and see session stats.

Note: macOS only for the clipboard command (pbpaste/pbcopy).
"""

import time
import sys
import os
from pathlib import Path

_LOG = Path.home() / ".preprompt" / "activity.log"
_DB  = Path.home() / ".preprompt" / "history.db"
_W   = 64


def _header() -> None:
    os.system("clear")
    print(f"  ⚡ PrePrompt — live activity feed")
    print(f"  watching ~/.preprompt/activity.log")
    print(f"  {'─' * 40}")
    print(f"  Ctrl+C to exit")
    print()


def _stats_line() -> str:
    """Pull quick stats from SQLite."""
    try:
        import sqlite3
        conn = sqlite3.connect(str(_DB), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        row = conn.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN was_intercepted=1 THEN 1 ELSE 0 END),
                   ROUND(AVG(classifier_score), 1)
            FROM prompt_history
        """).fetchone()
        conn.close()
        total, intercepted, avg = row
        intercepted = intercepted or 0
        pct = round(intercepted / total * 100) if total else 0
        return (
            f"  total={total}  intercepted={intercepted} "
            f"({pct}%)  avg_score={avg}"
        )
    except Exception:
        return "  stats unavailable"


def watch_cmd() -> None:
    _header()

    try:
        from storage.db import flush_pending_hook_events
        flushed = flush_pending_hook_events()
        if flushed > 0:
            print(f"  ↑ flushed {flushed} pending events from hook")
    except Exception:
        pass

    print(_stats_line())
    print()
    print(f"  {'─' * 40}")
    print()

    if not _LOG.exists():
        print("  No activity yet — send a prompt in Claude Code to start.")
        print()

    try:
        with open(_LOG, "a"):   # create if missing
            pass
        with open(_LOG, "r") as f:
            f.seek(0, 2)        # skip to end — show only new events
            while True:
                line = f.readline()
                if line:
                    if "INTERCEPTED" in line:
                        print(f"  \033[33m{line.rstrip()}\033[0m")
                    elif "passthrough" in line:
                        print(f"  \033[2m{line.rstrip()}\033[0m")
                    else:
                        print(f"  {line.rstrip()}")
                    sys.stdout.flush()
                else:
                    time.sleep(0.2)
    except KeyboardInterrupt:
        print()
        print()
        print(_stats_line())
        print()
        print("  PrePrompt session ended.")
        print()
