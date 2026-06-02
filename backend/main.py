"""
PrePrompt demo backend — deployed to Railway.
Exposes POST /api/demo for the landing page live demo widget.
"""

import os
import sys
import json
import httpx
import stripe
from pathlib import Path
from datetime import datetime

# Add repo root to path so we can import mcp_server and storage
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mcp_server.classifier import route_prompt
from mcp_server.optimizer import optimize
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

load_dotenv()

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

    routing = route_prompt(prompt, [], 1)
    route = routing["route"]
    score = routing["quality_score"]

    was_optimized = False
    if route in ("pass", "clarify"):
        result = {"optimized_prompt": prompt, "reason": routing["reason"], "changes_made": []}
    else:
        result = optimize(prompt, [])
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
