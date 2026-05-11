"""
PrePrompt demo backend — deployed to Railway.
Exposes POST /api/demo for the landing page live demo widget.
"""

import os
import sys
from pathlib import Path

# mcp_server lives one level up (project root)
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")

app = FastAPI(title="PrePrompt Demo API", version="0.1.8")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DEMO_LIMIT = 2


class DemoRequest(BaseModel):
    prompt: str


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _get_usage_count(ip: str) -> int:
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        return 0
    url = f"{SUPABASE_URL}/rest/v1/demo_usage?ip=eq.{ip}&select=count"
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
    }
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers, timeout=5)
        rows = r.json()
        if rows and isinstance(rows, list):
            return rows[0].get("count", 0)
    return 0


async def _upsert_usage(ip: str) -> int:
    """Upsert IP usage count and return the new count."""
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        return 1
    url = f"{SUPABASE_URL}/rest/v1/demo_usage"
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    payload = {"ip": ip, "count": 1, "last_used_at": "now()"}
    async with httpx.AsyncClient() as client:
        r = await client.post(url, json=payload, headers=headers, timeout=5)
        rows = r.json()
        # After upsert, fetch updated count
        count_url = f"{SUPABASE_URL}/rest/v1/demo_usage?ip=eq.{ip}&select=count"
        r2 = await client.get(count_url, headers={
            "apikey": SUPABASE_SECRET_KEY,
            "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        }, timeout=5)
        rows2 = r2.json()
        if rows2 and isinstance(rows2, list):
            return rows2[0].get("count", 1)
    return 1


@app.post("/api/demo")
async def demo(body: DemoRequest, request: Request) -> JSONResponse:
    prompt = body.prompt.strip()
    if not prompt:
        return JSONResponse({"error": "empty_prompt", "message": "Prompt cannot be empty."}, status_code=400)

    ip = _get_client_ip(request)

    # Check usage limit
    current_count = await _get_usage_count(ip)
    if current_count >= DEMO_LIMIT:
        return JSONResponse(
            {
                "error": "limit_reached",
                "message": "You've used your 2 free tries. Get 500/month for $8 →",
            },
            status_code=429,
        )

    # Run classifier + optimizer
    from mcp_server.classifier import route_prompt
    from mcp_server.optimizer import optimize

    routing = route_prompt(prompt, [], 1)
    route = routing["route"]
    score = routing["quality_score"]

    was_optimized = False
    if route == "clarify":
        result = {
            "optimized_prompt": prompt,
            "reason": routing["reason"],
            "changes_made": [],
        }
    elif route == "pass":
        result = {
            "optimized_prompt": prompt,
            "reason": routing["reason"],
            "changes_made": [],
        }
    else:
        result = optimize(prompt, [])
        was_optimized = result["optimized_prompt"] != prompt

    # Upsert usage
    new_count = await _upsert_usage(ip)
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
