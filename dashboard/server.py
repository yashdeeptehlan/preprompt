"""FastAPI dashboard server for PrePrompt — local web UI at http://localhost:7777."""

import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="PrePrompt Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_STATIC_DIR = Path(__file__).parent / "static"


def _serialize(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def _jsonify(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _jsonify(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_jsonify(item) for item in data]
    return _serialize(data)


@app.get("/api/stats")
async def stats() -> JSONResponse:
    from storage.db import get_feedback_stats, flush_pending_hook_events, get_read_connection
    try:
        flush_pending_hook_events()
    except Exception:
        pass
    conn = get_read_connection()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN was_intercepted THEN 1 ELSE 0 END) as intercepted,
                AVG(classifier_score) as avg_score,
                COUNT(DISTINCT session_id) as sessions
            FROM prompt_history
        """).fetchone()
        total, intercepted, avg_score, sessions = row
        total = total or 0
        intercepted = intercepted or 0
        pct = round(intercepted / total * 100, 1) if total else 0.0
        fb = get_feedback_stats()
        return JSONResponse(_jsonify({
            "total": total,
            "intercepted": intercepted,
            "intercepted_pct": pct,
            "avg_score": round(avg_score or 0.0, 1),
            "sessions": sessions or 0,
            "accept_rate": fb.get("accept_rate"),
            "kept": fb.get("kept", 0),
            "rejected": fb.get("rejected", 0),
        }))
    finally:
        conn.close()


@app.get("/api/history")
async def history(limit: int = 50, intercepted_only: bool = False) -> JSONResponse:
    from storage.db import get_all_history
    events = get_all_history(limit=limit, intercepted_only=intercepted_only)
    return JSONResponse(_jsonify(events))


@app.get("/api/routes")
async def routes() -> JSONResponse:
    from storage.db import get_route_stats
    return JSONResponse(get_route_stats())


@app.get("/api/memory")
async def memory() -> JSONResponse:
    from storage.db import get_stack_memory_with_confidence
    entries = get_stack_memory_with_confidence()
    return JSONResponse(_jsonify(entries))


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html_path = _STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>Dashboard not found — check installation</h1>", status_code=500)


def main() -> None:
    import uvicorn
    print()
    print("  PrePrompt Dashboard — http://localhost:7777")
    print("  Press Ctrl+C to stop")
    print()
    uvicorn.run(app, host="127.0.0.1", port=7777, log_level="warning")


if __name__ == "__main__":
    main()
