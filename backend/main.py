"""
PrePrompt demo backend — deployed to Railway.
Fully self-contained: no dependencies on parent mcp_server/ or storage/ packages.
Exposes POST /api/demo for the landing page live demo widget.
"""

import os
import json
import anthropic
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")
DEMO_LIMIT = 2

app = FastAPI(title="PrePrompt Demo API", version="0.1.8")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Inline classifier ─────────────────────────────────────────────────────────

_QUESTION_WORDS = {"what", "why", "how", "when", "where", "who", "which", "is", "are", "does", "do", "can", "should"}
_VAGUE_SHORT_VERBS = {"fix", "improve", "update", "change", "make", "add", "refactor", "clean", "optimize"}
_TECH_KEYWORDS = {
    "api", "auth", "oauth", "jwt", "middleware", "database", "db", "sql", "redis",
    "cache", "queue", "async", "await", "function", "class", "module", "service",
    "endpoint", "route", "test", "docker", "deploy", "migration", "schema",
    "react", "fastapi", "django", "flask", "express", "node", "typescript",
    "python", "golang", "rust", "postgres", "mongodb", "supabase", "prisma",
    "graphql", "rest", "grpc", "websocket", "ci", "cd", "github", "lambda",
    "rate", "limit", "limiter", "token", "refresh", "session", "cookie",
    "webhook", "cron", "worker", "pipeline", "stream", "buffer", "hook",
    "implement", "build", "create", "write", "generate", "refactor",
}


def _route_prompt(prompt: str) -> dict:
    words = prompt.lower().split()
    word_count = len(words)

    # PASS: very short or question
    if word_count <= 2:
        return {"route": "pass", "score": 5, "reason": "Prompt is too short to optimize."}
    if words[0] in _QUESTION_WORDS:
        return {"route": "pass", "score": 10, "reason": "Factual question — no rewrite needed."}

    has_tech = any(w in _TECH_KEYWORDS for w in words)

    # CLARIFY: vague + short + no technical content
    if word_count < 6 and not has_tech and words[0] in _VAGUE_SHORT_VERBS:
        return {
            "route": "clarify",
            "score": 20,
            "reason": "Prompt is too vague to optimize without more context.",
        }

    # ENRICH: everything else
    score = min(95, 30 + word_count * 3 + (25 if has_tech else 0))
    return {"route": "enrich", "score": score, "reason": "Prompt can be made more precise and actionable."}


# ── Inline optimizer ──────────────────────────────────────────────────────────

_SYSTEM = """You are PrePrompt, an expert prompt engineer. Your job is to rewrite a developer's prompt to be clearer, more specific, and more actionable for an AI coding assistant.

RULES:
- Preserve the original intent exactly — never change what the user is asking for
- Add specificity: output format, constraints, edge cases the user probably wants handled
- Keep it concise — do not pad with unnecessary words
- Return JSON only, no markdown fences

Output format:
{"optimized_prompt": "...", "reason": "one sentence on what you improved", "changes_made": ["change 1", "change 2"]}"""


def _optimize(prompt: str) -> dict:
    if not ANTHROPIC_API_KEY:
        return {"optimized_prompt": prompt, "reason": "No API key configured.", "changes_made": []}
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"Rewrite this prompt:\n\n{prompt}"}],
        )
        text = msg.content[0].text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        return {"optimized_prompt": prompt, "reason": f"Optimization unavailable: {e}", "changes_made": []}


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
    }


async def _verify_jwt(token: str) -> str | None:
    """Verify a Supabase JWT and return the user_id, or None if invalid."""
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"Authorization": f"Bearer {token}", "apikey": SUPABASE_SECRET_KEY},
                timeout=5,
            )
            if r.status_code == 200:
                return r.json().get("id")
    except Exception:
        pass
    return None


async def _get_usage_count(key: str) -> int:
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        return 0
    url = f"{SUPABASE_URL}/rest/v1/demo_usage?ip=eq.{key}&select=count"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=_sb_headers(), timeout=5)
        rows = r.json()
        if rows and isinstance(rows, list):
            return rows[0].get("count", 0)
    return 0


async def _upsert_usage(key: str) -> int:
    """Increment usage count for the given key and return the new count."""
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        return 1
    url = f"{SUPABASE_URL}/rest/v1/demo_usage"
    headers = {**_sb_headers(), "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
    payload = {"ip": key, "count": 1, "last_used_at": "now()"}
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload, headers=headers, timeout=5)
        r2 = await client.get(
            f"{SUPABASE_URL}/rest/v1/demo_usage?ip=eq.{key}&select=count",
            headers=_sb_headers(), timeout=5,
        )
        rows = r2.json()
        if rows and isinstance(rows, list):
            return rows[0].get("count", 1)
    return 1


# ── Routes ────────────────────────────────────────────────────────────────────

class DemoRequest(BaseModel):
    prompt: str


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/api/demo")
async def demo(body: DemoRequest, request: Request) -> JSONResponse:
    prompt = body.prompt.strip()
    if not prompt:
        return JSONResponse({"error": "empty_prompt", "message": "Prompt cannot be empty."}, status_code=400)

    # Prefer user-keyed tracking for authenticated requests
    auth_header = request.headers.get("Authorization", "")
    user_id: str | None = None
    if auth_header.startswith("Bearer "):
        user_id = await _verify_jwt(auth_header[7:])
    tracking_key = f"user:{user_id}" if user_id else _get_client_ip(request)

    current_count = await _get_usage_count(tracking_key)
    if current_count >= DEMO_LIMIT:
        return JSONResponse(
            {"error": "limit_reached", "message": "You've used your 2 free tries. Get 500/month for $8 →"},
            status_code=429,
        )

    routing = _route_prompt(prompt)
    route = routing["route"]
    score = routing["score"]

    was_optimized = False
    if route in ("pass", "clarify"):
        result = {"optimized_prompt": prompt, "reason": routing["reason"], "changes_made": []}
    else:
        result = _optimize(prompt)
        was_optimized = result["optimized_prompt"] != prompt

    new_count = await _upsert_usage(tracking_key)
    tries_remaining = max(0, DEMO_LIMIT - new_count)

    return JSONResponse({
        "original": prompt,
        "optimized": result["optimized_prompt"],
        "route": route,
        "score": score,
        "reason": result.get("reason", ""),
        "changes_made": result.get("changes_made", []),
        "was_optimized": was_optimized,
        "tries_remaining": tries_remaining,
    })


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "version": "0.1.8"})
