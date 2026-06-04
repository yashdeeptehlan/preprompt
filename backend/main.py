"""
PrePrompt demo backend — deployed to Railway.
Exposes POST /api/demo for the landing page live demo widget.
"""

import os
import re
import json
import httpx
import stripe
import anthropic
from datetime import datetime

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

load_dotenv()

# ── Inline classifier / optimizer ─────────────────────────────────────────────
# Railway deploys backend/ as an isolated container — the parent repo's
# mcp_server/ and storage/ packages are not available on $PYTHONPATH there.
# These inline implementations mirror mcp_server/classifier.py and
# mcp_server/optimizer.py exactly so the demo endpoint works in production.

_AMBIGUITY_VERBS = [
    "handle", "manage", "fix", "make it work", "deal with",
    "update", "improve", "refactor", "clean up", "sort out",
]
_CODE_KEYWORDS = [
    "function", "class", "api", "endpoint", "component", "middleware",
    "script", "write", "create", "implement", "build", "add", "set up",
]
_FORMAT_KEYWORDS = [
    "json", "list", "array", "string", "dict", "object",
    "html", "markdown", "table", "as a ", "return type", "output format",
]
_CONVERSATIONAL = {
    "yes", "no", "ok", "okay", "thanks", "thank", "cool",
    "great", "sure", "alright", "yep", "nope", "gotcha",
}
_OPTIMIZATION_THRESHOLD = 38
_VAGUE_ACTION_VERBS = frozenset({
    "make", "fix", "improve", "update", "change", "do", "help", "clean", "sort",
})
_MULTI_WORD_VAGUE_PHRASES = frozenset({
    "make it", "make this", "make that", "make it better", "make this better",
    "make that better", "make it work", "make this work", "fix it", "fix this",
    "fix that", "improve it", "improve this", "improve that", "clean up",
    "sort out", "help me", "do this", "do that",
})
_QUESTION_STARTERS = frozenset({
    "what", "how", "why", "when", "where", "is", "does", "can", "should",
})
_GENERIC_OBJECTS = frozenset({
    "it", "this", "that", "them", "things", "everything", "stuff",
    "bug", "issue", "error", "problem", "code", "file", "thing",
})
_STOP_WORDS = frozenset({
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "with",
    "that", "this", "be", "by", "or", "and", "but", "not", "from",
    "as", "into", "about",
})
_CLEAR_ACTION_VERBS = frozenset({
    "implement", "refactor", "migrate", "deploy", "configure", "integrate",
    "optimize", "debug", "test", "write", "create", "build", "add", "remove",
    "delete", "update", "fix",
})

_OPTIMIZE_SYSTEM = """\
You are an expert prompt engineer embedded in a developer's IDE. Your job is to take a
user's raw prompt and rewrite it so it is clearer, more specific, and more likely to
produce a high-quality response from a coding assistant.

CORE RULE — INTENT PRESERVATION:
Your rewrite must preserve the user's original intent exactly. You improve HOW the
prompt is expressed, never WHAT it asks for.

HARD CONSTRAINTS — never violate these:
1. Do NOT expand the task scope. "fix the bug" must not become "refactor the system".
2. Do NOT add unrequested features, libraries, or architectural changes.
3. Do NOT change the task type. A fix stays a fix. A question stays a question.
4. Do NOT assume target files, components, or frameworks unless present in history.
5. For bug-fix prompts: default to "smallest safe fix".
6. For bug-fix prompts: add "do not change unrelated files" unless user said otherwise.
7. If you must add an assumption to make the prompt executable, label it explicitly
   in changes_made as "Assumption added: <what you assumed>".
8. Never make the prompt sound more ambitious than the user intended.

Return a JSON object with exactly these keys:
  "optimized_prompt" : the rewritten prompt (string)
  "reason"           : one sentence explaining the main improvement (string)
  "changes_made"     : list of short strings, each describing one specific change

Respond ONLY with valid JSON. No markdown fences, no extra commentary.\
"""


def _has_technical_noun(words: list) -> bool:
    return any(
        len(w) > 4 and w not in _GENERIC_OBJECTS and w not in _STOP_WORDS
        for w in words
    )


def _classify_prompt(prompt: str) -> int:
    score = 0
    lower = prompt.lower().strip()
    words = lower.split()
    word_count = len(words)

    has_numbered_steps = bool(re.search(r"(?:^|\s)\d+\.\s", prompt))
    has_explicit_format = any(kw in lower for kw in _FORMAT_KEYWORDS)
    has_code_task = any(kw in lower for kw in _CODE_KEYWORDS)
    has_tech_noun = _has_technical_noun(words)

    if words and words[0] in _CONVERSATIONAL:
        score -= 25
    if word_count < 4 and not has_code_task and not has_tech_noun:
        score -= 20
    if re.match(r"^what (is|does|are)\b", lower):
        score -= 15
    if has_numbered_steps or has_explicit_format:
        score -= 15

    ambiguity_hits = sum(1 for v in _AMBIGUITY_VERBS if v in lower)
    score += min(ambiguity_hits * 25, 25)
    and_count = lower.count(" and ")
    comma_count = lower.count(",")
    score += min((and_count + comma_count) * 12, 30)
    if has_code_task and not has_explicit_format:
        score += 15
    if has_tech_noun:
        score += 25
    if 2 <= word_count <= 6 and words and words[0] in _CLEAR_ACTION_VERBS and has_tech_noun:
        score += 15

    return score


def _route_prompt(prompt: str) -> dict:
    score = _classify_prompt(prompt)
    lower = prompt.lower().strip()
    words = lower.split()
    word_count = len(words)

    # Clarify check
    is_clarify = False
    missing_context: list = []
    if lower.strip() in _MULTI_WORD_VAGUE_PHRASES:
        is_clarify, missing_context = True, ["target area", "desired outcome"]
    elif word_count < 4 and score >= _OPTIMIZATION_THRESHOLD and not _has_technical_noun(words):
        is_clarify, missing_context = True, ["target area", "desired outcome"]
    elif word_count <= 5 and words and words[0] in _VAGUE_ACTION_VERBS:
        meaningful = [w for w in words[1:] if w not in {"the", "a", "an", "my", "our", "your"}]
        if not meaningful:
            is_clarify, missing_context = True, ["target area", "desired outcome"]
        elif len(meaningful) <= 2 and all(w in _GENERIC_OBJECTS for w in meaningful):
            has_bug_word = any(w in {"bug", "issue", "error", "problem"} for w in meaningful)
            missing_context = ["target file or component", "desired outcome"] if has_bug_word \
                else ["target area", "desired outcome"]
            is_clarify = True

    if is_clarify:
        return {
            "route": "clarify",
            "quality_score": score,
            "reason": "Prompt is too vague to optimize safely without risking scope expansion.",
            "missing_context": missing_context,
        }

    is_question = bool(words and words[0] in _QUESTION_STARTERS)
    if score < _OPTIMIZATION_THRESHOLD or (is_question and score < 50):
        return {
            "route": "pass",
            "quality_score": score,
            "reason": f"Score {score} is below threshold {_OPTIMIZATION_THRESHOLD}; prompt is clear enough.",
            "missing_context": [],
        }

    return {
        "route": "enrich",
        "quality_score": score,
        "reason": "Prompt can be enriched with more technical specificity and context.",
        "missing_context": [],
    }


def _optimize(prompt: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"optimized_prompt": prompt, "reason": "API key not configured.", "changes_made": []}
    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=httpx.Timeout(5.0, connect=2.0))
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=_OPTIMIZE_SYSTEM,
            messages=[{"role": "user", "content": f"Original prompt:\n{prompt}"}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            raw = raw[raw.find("\n") + 1:]
            raw = raw.rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        return {
            "optimized_prompt": data.get("optimized_prompt", prompt),
            "reason": data.get("reason", ""),
            "changes_made": data.get("changes_made", []),
        }
    except Exception:
        return {"optimized_prompt": prompt, "reason": "Optimization unavailable; original prompt returned.", "changes_made": []}


SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_SOLO_PRICE_ID = os.environ.get("STRIPE_SOLO_PRICE_ID", "")
STRIPE_PRO_PRICE_ID = os.environ.get("STRIPE_PRO_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
DEMO_LIMIT = 2

stripe.api_key = STRIPE_SECRET_KEY

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="PrePrompt Demo API", version="0.1.9")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Security dependencies ─────────────────────────────────────────────────────

def verify_origin(request: Request):
    origin = request.headers.get("origin") or request.headers.get("referer", "")
    allowed = os.environ.get("ALLOWED_ORIGINS", "https://preprompt.org").split(",")
    allowed = [o.strip() for o in allowed]
    if "localhost" in origin or "127.0.0.1" in origin:
        return True
    if not any(origin.startswith(a) for a in allowed):
        raise HTTPException(status_code=403, detail="Origin not allowed")
    return True


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


# ── Email ─────────────────────────────────────────────────────────────────────

async def send_thankyou_email(email: str, plan: str) -> None:
    resend_key = os.environ.get("RESEND_API_KEY", "")
    if not resend_key:
        print(f"[PrePrompt] RESEND_API_KEY not set, skipping email to {email}")
        return

    plan_name = "Solo" if plan == "solo" else "Pro"
    price = "$8/month" if plan == "solo" else "$19/month"

    email_body = f"""Hey,

Welcome to PrePrompt {plan_name} — glad you're here.

You're now set up with {price} worth of prompt optimization. Here's how to get started:

1. Install PrePrompt:

   pip install preprompt

2. Run setup:

   preprompt-install

That's it. PrePrompt will now silently intercept and optimize your prompts in Claude Code and Cursor before they reach the model.

If you run into anything, just reply to this email.

— Yashdeep

Founder, PrePrompt

preprompt.org"""

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": "Yashdeep from PrePrompt <yashdeep@preprompt.org>",
                    "to": [email],
                    "subject": f"Welcome to PrePrompt {plan_name}",
                    "text": email_body,
                },
            )
            print(f"[PrePrompt] Email sent to {email}: {response.status_code} {response.text}")
    except Exception as e:
        print(f"[PrePrompt] Email failed for {email}: {e}")


# ── Routes ────────────────────────────────────────────────────────────────────

class DemoRequest(BaseModel):
    prompt: str


class CheckoutRequest(BaseModel):
    plan: str
    user_id: str
    email: str


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/api/demo")
@limiter.limit("10/minute")
async def demo(request: Request, body: DemoRequest, _o=Depends(verify_origin)) -> JSONResponse:
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
    score = routing["quality_score"]

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


@app.post("/api/create-checkout-session")
async def create_checkout_session(body: CheckoutRequest, _o=Depends(verify_origin)) -> JSONResponse:
    if body.plan not in ("solo", "pro"):
        raise HTTPException(status_code=400, detail="Invalid plan")
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Payments not configured")

    price_id = STRIPE_SOLO_PRICE_ID if body.plan == "solo" else STRIPE_PRO_PRICE_ID
    if not price_id:
        raise HTTPException(status_code=503, detail="Price not configured")

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            customer_email=body.email,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url="https://preprompt.org/success.html?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://preprompt.org",
            metadata={"user_id": body.user_id, "plan": body.plan},
            subscription_data={"metadata": {"user_id": body.user_id, "plan": body.plan}},
        )
        return JSONResponse({"checkout_url": session.url})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/webhook")
async def stripe_webhook(request: Request) -> JSONResponse:
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid signature")
    else:
        event = json.loads(payload)

    if event["type"] == "checkout.session.completed":
        try:
            session = event["data"]["object"]
            metadata = session.metadata.to_dict() if session.metadata else {}
            user_id = metadata.get("user_id")
            plan = metadata.get("plan", "solo")
            customer_id = session.customer
            subscription_id = session.subscription
            customer_email = session.customer_details.email if session.customer_details else None

            if user_id and SUPABASE_URL and SUPABASE_SECRET_KEY:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{SUPABASE_URL}/rest/v1/user_profiles",
                        headers={
                            "apikey": SUPABASE_SECRET_KEY,
                            "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
                            "Content-Type": "application/json",
                            "Prefer": "resolution=merge-duplicates",
                        },
                        json={
                            "id": user_id,
                            "plan": plan,
                            "stripe_customer_id": customer_id,
                            "stripe_subscription_id": subscription_id,
                            "subscription_status": "active",
                            "subscription_started_at": datetime.utcnow().isoformat(),
                            "demo_tries_used": 0,
                        },
                        timeout=10,
                    )

            if customer_email:
                try:
                    await send_thankyou_email(customer_email, plan)
                except Exception as email_err:
                    print(f"[PrePrompt] Email error: {email_err}")

        except Exception as webhook_err:
            print(f"[PrePrompt] Webhook processing error: {webhook_err}")
            import traceback
            traceback.print_exc()

    return JSONResponse({"status": "ok"})


@app.get("/api/verify-session")
async def verify_session(session_id: str) -> JSONResponse:
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Payments not configured")
    try:
        import logging
        logging.info(f"Verifying session: {session_id[:20]}...")
        session = stripe.checkout.Session.retrieve(session_id)
        logging.info(f"Session status: {session.status}, payment: {session.payment_status}")
        return JSONResponse({
            "success": True,
            "plan": (session.metadata["plan"] if session.metadata and "plan" in session.metadata else "solo"),
            "email": (session.customer_details.email if session.customer_details and hasattr(session.customer_details, 'email') else ""),
        })
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error: {type(e).__name__}: {str(e)}")


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "version": "0.1.9"})
