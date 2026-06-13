"""Weekly metrics report — Monday morning summary for the team.

Reads the local SQLite history DB (or, in production, will be swapped for the
hosted Supabase / PostHog source) and emits a markdown report to stdout.

Recommended usage:

    # Locally
    python -m scripts.weekly_metrics

    # In cron — append to weekly archive AND mail to the team
    0 9 * * MON cd /path/to/preprompt && \\
        python -m scripts.weekly_metrics | \\
        tee -a ~/.preprompt/weekly.log | \\
        mail -s "PrePrompt weekly $(date +\\%Y-\\%m-\\%d)" team@preprompt.ai

The script is deliberately data-source-agnostic: swap ``_load_events`` for a
PostHog or Supabase fetcher once production telemetry is wired.
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DB_PATH = Path.home() / ".preprompt" / "history.db"


def _load_events(db_path: Path, since: datetime) -> list[dict]:
    """Pull prompt events newer than ``since``. ISO timestamps stored as text."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT timestamp, was_intercepted, classifier_score,
                   COALESCE(route, 'enrich') AS route, user_kept, session_id
            FROM prompt_history
            WHERE timestamp >= ?
            """,
            [since.isoformat()],
        ).fetchall()
    finally:
        conn.close()
    cols = ["timestamp", "was_intercepted", "classifier_score", "route", "user_kept", "session_id"]
    return [dict(zip(cols, r)) for r in rows]


def _summarize(events: list[dict]) -> dict:
    """Aggregate a week of events into the numbers we report on."""
    total = len(events)
    intercepted = sum(1 for e in events if e["was_intercepted"])
    sessions = len({e["session_id"] for e in events if e["session_id"]})
    routes = Counter(e["route"] for e in events)
    rated_kept = sum(1 for e in events if e["user_kept"] == 1)
    rated_rej  = sum(1 for e in events if e["user_kept"] == 0)
    rated_total = rated_kept + rated_rej
    accept_rate = round(rated_kept / rated_total * 100) if rated_total else None
    avg_score = (sum(e["classifier_score"] or 0 for e in events) / total) if total else 0.0
    return {
        "total": total,
        "intercepted": intercepted,
        "intercept_pct": round(intercepted / total * 100, 1) if total else 0.0,
        "sessions": sessions,
        "routes": dict(routes),
        "kept": rated_kept,
        "rejected": rated_rej,
        "accept_rate": accept_rate,
        "avg_score": round(avg_score, 1),
    }


def _delta(curr: float | None, prev: float | None) -> str:
    """Render a week-over-week delta as ``+/-N`` or em-dash when prior is unknown."""
    if curr is None or prev is None:
        return "—"
    d = round(curr - prev, 1)
    if d == 0:
        return "→ 0"
    return f"{'▲' if d > 0 else '▼'} {abs(d)}"


def _render(this_week: dict, last_week: dict, week_start: datetime) -> str:
    """Format the report as markdown."""
    lines: list[str] = []
    lines.append(f"# PrePrompt — weekly report (week of {week_start.date().isoformat()})\n")
    lines.append(f"**Source:** `{_DB_PATH}`  ")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")

    lines.append("## Volume\n")
    lines.append(f"- Prompts processed: **{this_week['total']}** ({_delta(this_week['total'], last_week['total'])} vs prior week)")
    lines.append(f"- Unique sessions: **{this_week['sessions']}** ({_delta(this_week['sessions'], last_week['sessions'])})")
    lines.append(f"- Intercepted: **{this_week['intercepted']}** ({this_week['intercept_pct']}% of total)\n")

    lines.append("## Quality\n")
    lines.append(f"- Avg classifier score: **{this_week['avg_score']}** ({_delta(this_week['avg_score'], last_week['avg_score'])})")
    if this_week["accept_rate"] is not None:
        lines.append(
            f"- Accept rate: **{this_week['accept_rate']}%** "
            f"({this_week['kept']} kept / {this_week['rejected']} rejected) "
            f"({_delta(this_week['accept_rate'], last_week['accept_rate'])})"
        )
    else:
        lines.append("- Accept rate: _no feedback this week_")
    lines.append("")

    lines.append("## Route breakdown\n")
    total = this_week["total"] or 1
    for r in ("pass", "enrich", "clarify"):
        n = this_week["routes"].get(r, 0)
        pct = round(n / total * 100, 1)
        lines.append(f"- `{r}`: **{n}** ({pct}%)")
    lines.append("")

    if this_week["total"] == 0:
        lines.append("> No activity this week. Verify the hook is registered and the API key is set.\n")

    return "\n".join(lines)


def main() -> int:
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    prior_week_start = now - timedelta(days=14)

    this_week_events = _load_events(_DB_PATH, since=week_start)
    last_week_events = [e for e in _load_events(_DB_PATH, since=prior_week_start)
                        if datetime.fromisoformat(e["timestamp"]) < week_start]

    this_week = _summarize(this_week_events)
    last_week = _summarize(last_week_events)

    sys.stdout.write(_render(this_week, last_week, week_start))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
