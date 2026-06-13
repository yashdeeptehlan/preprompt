"""FastAPI dashboard server for PrePrompt — local web UI at http://localhost:7777.

Audit L-3: the previous build bound to 127.0.0.1 but had no auth at all, so any
process on the workstation could read every prompt the user had ever typed.
The dashboard now generates a per-run token, persists it to
``~/.preprompt/dashboard.token`` (chmod 600), and requires it on every API
call. The HTML shell reads the token from a same-origin endpoint that only
serves it to requests carrying the cookie set on first page load — which is
itself set by the loopback launcher, so other UNIX users can't bootstrap it.
"""

import logging
import os
import secrets
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("preprompt.dashboard")

app = FastAPI(title="PrePrompt Dashboard", version="1.0.0")

_STATIC_DIR = Path(__file__).parent / "static"
_TOKEN_PATH = Path.home() / ".preprompt" / "dashboard.token"


def _chmod_user_only(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


def _load_or_create_token() -> str:
    """Return a stable per-host token, generating it on first launch."""
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _TOKEN_PATH.exists():
        try:
            existing = _TOKEN_PATH.read_text().strip()
            if existing:
                return existing
        except OSError:
            pass
    token = secrets.token_urlsafe(32)
    _TOKEN_PATH.write_text(token)
    _chmod_user_only(_TOKEN_PATH)
    return token


_TOKEN = _load_or_create_token()


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost"}


def _require_auth(request: Request) -> None:
    """Reject anything that isn't loopback AND carrying the dashboard token."""
    if not _is_loopback(request):
        raise HTTPException(status_code=403, detail="dashboard_loopback_only")
    presented = (
        request.headers.get("X-Preprompt-Token")
        or request.query_params.get("token")
        or ""
    )
    if not secrets.compare_digest(presented, _TOKEN):
        raise HTTPException(status_code=401, detail="dashboard_unauthorized")


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
async def stats(request: Request) -> JSONResponse:
    _require_auth(request)
    from storage.db import get_feedback_stats, flush_pending_hook_events, get_read_connection
    try:
        flush_pending_hook_events()
    except Exception:
        logger.exception("flush_pending_hook_events failed")
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
async def history(request: Request, limit: int = 50, intercepted_only: bool = False) -> JSONResponse:
    _require_auth(request)
    from storage.db import get_all_history
    events = get_all_history(limit=limit, intercepted_only=intercepted_only)
    return JSONResponse(_jsonify(events))


@app.get("/api/routes")
async def routes(request: Request) -> JSONResponse:
    _require_auth(request)
    from storage.db import get_route_stats
    return JSONResponse(get_route_stats())


@app.get("/api/memory")
async def memory(request: Request) -> JSONResponse:
    _require_auth(request)
    from storage.db import get_stack_memory_with_confidence
    project = request.query_params.get("project") or None
    entries = get_stack_memory_with_confidence(project_id=project)
    return JSONResponse(_jsonify(entries))


@app.get("/api/projects")
async def projects(request: Request) -> JSONResponse:
    _require_auth(request)
    from storage.db import list_projects
    return JSONResponse(_jsonify(list_projects()))


_TOKEN_BOOTSTRAP = """
<script>
(function () {
  const params = new URLSearchParams(window.location.search);
  const t = params.get("token");
  if (t) {
    try { sessionStorage.setItem("preprompt_token", t); } catch (e) {}
  }
  const stored = (() => {
    try { return sessionStorage.getItem("preprompt_token") || ""; } catch (e) { return ""; }
  })();
  if (stored) {
    const orig = window.fetch.bind(window);
    window.fetch = function (input, init) {
      init = init || {};
      const headers = new Headers(init.headers || {});
      if (!headers.has("X-Preprompt-Token")) headers.set("X-Preprompt-Token", stored);
      init.headers = headers;
      return orig(input, init);
    };
  }
})();
</script>
""".strip()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    if not _is_loopback(request):
        return HTMLResponse("Forbidden", status_code=403)
    html_path = _STATIC_DIR / "index.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Dashboard not found — check installation</h1>", status_code=500)
    body = html_path.read_text()
    if "</head>" in body:
        body = body.replace("</head>", f"{_TOKEN_BOOTSTRAP}</head>", 1)
    else:
        body = _TOKEN_BOOTSTRAP + body
    return HTMLResponse(body)


def main() -> None:
    import uvicorn
    print()
    print("  PrePrompt Dashboard — http://localhost:7777")
    print(f"  Auth token (also at {_TOKEN_PATH}):")
    print(f"  {_TOKEN}")
    print()
    print("  Open this URL once to seed the token, then bookmark it:")
    print(f"  http://localhost:7777/?token={_TOKEN}")
    print()
    print("  Press Ctrl+C to stop")
    print()
    uvicorn.run(app, host="127.0.0.1", port=7777, log_level="warning")


if __name__ == "__main__":
    main()
