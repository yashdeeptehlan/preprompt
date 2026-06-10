"""
PrePrompt demo backend — deployed to Railway.
Exposes POST /api/demo for the landing page live demo widget.
"""

import os
import re
import json
import time
import uuid
import logging
import ipaddress
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import httpx
import stripe
import anthropic
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────
# Format includes a correlation_id slot; records without one get "-" via the
# filter below so we never raise KeyError inside logging.

class _CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = "-"
        return True


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s [%(correlation_id)s] %(message)s",
)
for _h in logging.getLogger().handlers:
    _h.addFilter(_CorrelationFilter())

logger = logging.getLogger("preprompt.backend")


# ── Sentry ────────────────────────────────────────────────────────────────────

_PII_KEYS = frozenset({
    "prompt", "optimized", "optimized_prompt", "user_prompt",
    "conversation_history", "original_prompt", "email",
    "customer_email", "api_key", "anthropic_api_key",
})


def _scrub_dict(obj):
    """Recursively redact PII keys anywhere in a Sentry event payload."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in _PII_KEYS:
                out[k] = "[REDACTED]"
            else:
                out[k] = _scrub_dict(v)
        return out
    if isinstance(obj, list):
        return [_scrub_dict(v) for v in obj]
    return obj


def _scrub_sensitive(event: dict, _hint: dict | None = None) -> dict:
    """Strip prompts, secrets, stack-frame locals from Sentry events."""
    try:
        for frame_root in ("exception", "threads"):
            entries = (event.get(frame_root) or {}).get("values") or []
            for entry in entries:
                for frame in (entry.get("stacktrace") or {}).get("frames") or []:
                    frame.pop("vars", None)
        event = _scrub_dict(event)
    except Exception:
        logger.exception("sentry scrub failed")
    return event


_SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if _SENTRY_DSN:
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        integrations=[FastApiIntegration(), HttpxIntegration()],
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        environment=os.environ.get("RAILWAY_ENVIRONMENT", "production"),
        send_default_pii=False,
        include_local_variables=False,
        before_send=_scrub_sensitive,
        before_send_transaction=_scrub_sensitive,
    )

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


_SECRET_PATTERNS = [
    # Tightened patterns — see mcp_server/secret_scanner.py for the canonical
    # version. These must stay in sync; the backend ships an isolated copy
    # because Railway does not have the mcp_server package on PYTHONPATH.
    ("aws_access_key",     re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws_secret_key",     re.compile(r"(?is)aws[A-Za-z0-9_\-]*secret[^\n]{0,60}?\b[A-Za-z0-9/+]{40}\b")),
    ("anthropic_api_key",  re.compile(r"\bsk-ant-(?:api|admin)[A-Za-z0-9_\-]{20,}\b")),
    ("openai_api_key",     re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{40,}\b")),
    ("github_token",       re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{36,}\b")),
    ("stripe_key",         re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{24,}\b")),
    ("private_key_header", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("bearer_token",       re.compile(r"\b[Bb]earer\s+[A-Za-z0-9\-._~+/]{20,}=*")),
    ("generic_api_key",    re.compile(r"(?i)\bapi[_-]?key\b\s*[:=]\s*['\"][A-Za-z0-9\-_]{20,}['\"]")),
    ("password_pattern",   re.compile(r"(?i)(?<![A-Za-z])password\s*[:=]\s*['\"][^\s'\"]{8,}['\"]")),
    ("connection_string",  re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:@]+:[^\s:@]+@\S+")),
]


def _scan_for_secrets(text: str) -> list[str]:
    return [name for name, pattern in _SECRET_PATTERNS if pattern.search(text)]


_CONTROL_SEQ_RE = re.compile(r"(?:<\|[^|]{0,40}\|>|\{\{[^}]{0,40}\}\}|<\|im_(?:start|end)\|>)")


def _sanitize_history(history: list) -> list:
    """Remove role-injection markers from history before sending to the model."""
    cleaned = []
    for turn in history or []:
        if not isinstance(turn, dict):
            continue
        content = str(turn.get("content", ""))
        cleaned.append({
            "role": turn.get("role", "user"),
            "content": _CONTROL_SEQ_RE.sub("", content),
        })
    return cleaned


_MAX_OPTIMIZED_BLOWUP = 4  # reject rewrites that explode prompt length


def _validate_optimizer_output(original: str, candidate: str) -> tuple[str, str]:
    """Return (sanitized_optimized, reason).

    Drop candidates that look like prompt-injection success (length blowup or
    rediscovered secrets that weren't in the original). Falls back to the
    original prompt with a reason on rejection.
    """
    if not isinstance(candidate, str) or not candidate.strip():
        return original, "Optimizer returned empty output; original prompt used."
    if len(candidate) > _MAX_OPTIMIZED_BLOWUP * max(len(original), 64):
        return original, "Optimizer output exceeded length budget; original prompt used."
    leaked = set(_scan_for_secrets(candidate)) - set(_scan_for_secrets(original))
    if leaked:
        logger.warning("optimizer output added new secret-like content: %s", sorted(leaked))
        return original, "Optimizer output contained sensitive content; original prompt used."
    return candidate, ""


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
        candidate = data.get("optimized_prompt", prompt)
        sanitized, override_reason = _validate_optimizer_output(prompt, candidate)
        return {
            "optimized_prompt": sanitized,
            "reason": override_reason or data.get("reason", ""),
            "changes_made": [] if override_reason else data.get("changes_made", []),
        }
    except Exception:
        logger.exception("optimizer call failed")
        return {"optimized_prompt": prompt, "reason": "Optimization unavailable; original prompt returned.", "changes_made": []}


SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_SOLO_PRICE_ID = os.environ.get("STRIPE_SOLO_PRICE_ID", "")
STRIPE_PRO_PRICE_ID = os.environ.get("STRIPE_PRO_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
DEMO_LIMIT = int(os.environ.get("DEMO_LIMIT", "2"))

stripe.api_key = STRIPE_SECRET_KEY


# ── Trusted reverse proxies ───────────────────────────────────────────────────
# X-Forwarded-For is only honoured when the request comes from one of these
# networks. Railway publishes its egress ranges; configure via env so we can
# update without a redeploy. Localhost is trusted by default for dev.

_DEFAULT_TRUSTED_PROXIES = "127.0.0.1/32,::1/128"


def _parse_networks(raw: str) -> list:
    nets = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            nets.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            logger.warning("ignoring invalid TRUSTED_PROXIES entry: %s", token)
    return nets


_TRUSTED_PROXIES = _parse_networks(
    os.environ.get("TRUSTED_PROXIES", _DEFAULT_TRUSTED_PROXIES)
)


def _peer_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _is_trusted_proxy(addr: str) -> bool:
    if not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in _TRUSTED_PROXIES)


# ── Origin allow-list ─────────────────────────────────────────────────────────


def _parse_origins(raw: str) -> list[str]:
    origins = []
    for token in raw.split(","):
        token = token.strip().rstrip("/")
        if token:
            origins.append(token)
    return origins


_ALLOWED_ORIGINS = _parse_origins(
    os.environ.get("ALLOWED_ORIGINS", "https://preprompt.org")
)
_DEV_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _allowed_origin_hosts() -> set[str]:
    hosts = set()
    for origin in _ALLOWED_ORIGINS:
        parsed = urlparse(origin)
        if parsed.hostname:
            hosts.add(parsed.hostname.lower())
    return hosts


def _origin_is_allowed(origin: str) -> bool:
    if not origin:
        return False
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if host in _DEV_LOOPBACK_HOSTS and os.environ.get("ALLOW_LOCAL_ORIGINS", "0") == "1":
        return True
    return host in _allowed_origin_hosts()


def _client_ip_for_limit(request: Request) -> str:
    """SlowAPI key function — only honour XFF from trusted proxies."""
    peer = _peer_ip(request)
    if _is_trusted_proxy(peer):
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            # Walk right-to-left, drop proxies we trust until we find the client.
            hops = [h.strip() for h in forwarded.split(",") if h.strip()]
            for hop in reversed(hops):
                if not _is_trusted_proxy(hop):
                    return hop
            if hops:
                return hops[0]
    return peer or "unknown"


limiter = Limiter(key_func=_client_ip_for_limit)
app = FastAPI(title="PrePrompt Demo API", version="0.1.9")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS or ["https://preprompt.org"],
    allow_origin_regex=None,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,
    max_age=600,
)


# ── Correlation IDs + error envelope ──────────────────────────────────────────

@app.middleware("http")
async def _correlation_middleware(request: Request, call_next):
    cid = request.headers.get("X-Preprompt-Trace-Id") or uuid.uuid4().hex[:16]
    request.state.correlation_id = cid
    extra = {"correlation_id": cid}
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled error", extra=extra)
        return JSONResponse(
            {"error": "internal_error", "correlation_id": cid},
            status_code=500,
            headers={"X-Preprompt-Trace-Id": cid},
        )
    response.headers["X-Preprompt-Trace-Id"] = cid
    return response


# ── Security dependencies ─────────────────────────────────────────────────────

def verify_origin(request: Request):
    origin = request.headers.get("origin") or request.headers.get("referer", "")
    if not _origin_is_allowed(origin):
        raise HTTPException(status_code=403, detail="origin_not_allowed")
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


_TRACKING_KEY_RE = re.compile(r"^(?:user:[A-Za-z0-9\-]{8,64}|ip:[A-Za-z0-9:.\-]{2,45})$")


def _safe_tracking_key(raw: str) -> str:
    """Return a normalised tracking key, falling back to a static bucket on garbage."""
    candidate = raw if raw.startswith(("user:", "ip:")) else f"ip:{raw}"
    if _TRACKING_KEY_RE.match(candidate):
        return candidate
    logger.warning("rejecting malformed tracking key %r — using unknown bucket", raw)
    return "ip:unknown"


async def _get_usage_count(key: str) -> int:
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        return 0
    encoded = quote(key, safe="")
    url = f"{SUPABASE_URL}/rest/v1/demo_usage?ip=eq.{encoded}&select=count"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=_sb_headers(), timeout=5)
        rows = r.json()
        if rows and isinstance(rows, list):
            return rows[0].get("count", 0)
    return 0


async def _upsert_usage(key: str, current_count: int) -> int:
    """Increment usage count and return the new count.

    Prefers the ``increment_demo_usage`` Supabase RPC for an atomic
    SELECT+UPDATE round-trip. Falls back to the previous POST/PATCH flow when
    the RPC is unavailable so existing deployments keep working.
    """
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        return current_count + 1

    new_count = current_count + 1
    encoded = quote(key, safe="")
    rpr_headers = {**_sb_headers(), "Content-Type": "application/json", "Prefer": "return=representation"}

    async with httpx.AsyncClient(timeout=5) as client:
        try:
            rpc = await client.post(
                f"{SUPABASE_URL}/rest/v1/rpc/increment_demo_usage",
                json={"p_key": key, "p_limit": DEMO_LIMIT},
                headers={**_sb_headers(), "Content-Type": "application/json"},
            )
            if rpc.status_code == 200:
                data = rpc.json()
                if isinstance(data, dict) and "count" in data:
                    return int(data["count"])
                if isinstance(data, int):
                    return int(data)
        except Exception:
            logger.exception("increment_demo_usage RPC failed; falling back")

        if current_count == 0:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/demo_usage",
                json={"ip": key, "count": 1, "last_used_at": "now()"},
                headers=rpr_headers,
            )
        else:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/demo_usage?ip=eq.{encoded}",
                json={"count": new_count, "last_used_at": "now()"},
                headers=rpr_headers,
            )

        rows = r.json()
        if rows and isinstance(rows, list) and rows:
            return rows[0].get("count", new_count)
    return new_count


# ── Email ─────────────────────────────────────────────────────────────────────

def _mask_email(email: str) -> str:
    if not email or "@" not in email:
        return "[redacted]"
    local, _, domain = email.partition("@")
    masked_local = (local[:1] + "***") if local else "***"
    return f"{masked_local}@{domain}"


async def send_thankyou_email(email: str, plan: str) -> None:
    resend_key = os.environ.get("RESEND_API_KEY", "")
    if not resend_key:
        logger.info("RESEND_API_KEY not set; skipping welcome email to %s", _mask_email(email))
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
            logger.info(
                "welcome email to %s: status=%s",
                _mask_email(email),
                response.status_code,
            )
    except Exception:
        logger.exception("welcome email send failed for %s", _mask_email(email))


# ── Routes ────────────────────────────────────────────────────────────────────

class DemoRequest(BaseModel):
    prompt: str


class CheckoutRequest(BaseModel):
    plan: str
    user_id: str
    email: str


def _get_client_ip(request: Request) -> str:
    return _client_ip_for_limit(request)


@app.post("/api/demo")
@limiter.limit("5/minute")
@limiter.limit("20/day")
async def demo(request: Request, body: DemoRequest, _o=Depends(verify_origin)) -> JSONResponse:
    _t0 = time.monotonic()
    prompt = body.prompt.strip()
    if not prompt:
        return JSONResponse({"error": "empty_prompt", "message": "Prompt cannot be empty."}, status_code=400)

    # Prefer user-keyed tracking for authenticated requests
    auth_header = request.headers.get("Authorization", "")
    user_id: str | None = None
    if auth_header.startswith("Bearer "):
        user_id = await _verify_jwt(auth_header[7:])
    raw_key = f"user:{user_id}" if user_id else f"ip:{_get_client_ip(request)}"
    tracking_key = _safe_tracking_key(raw_key)

    # Step 1: enforce limit server-side BEFORE any processing
    current_count = await _get_usage_count(tracking_key)
    if current_count >= DEMO_LIMIT:
        from analytics import track
        track("free_tier_limit_reached", user_id, {
            "ip_hash": hash(_get_client_ip(request)) % 1_000_000,
            "tries_used": current_count,
        })
        return JSONResponse(
            {
                "error": "limit_reached",
                "message": f"You've used your {DEMO_LIMIT} free tries. Get 500/month for $8 →",
                "tries_used": current_count,
                "limit": DEMO_LIMIT,
            },
            status_code=429,
        )

    routing = _route_prompt(prompt)
    route = routing["route"]
    score = routing["quality_score"]

    was_optimized = False
    if route in ("pass", "clarify"):
        result = {"optimized_prompt": prompt, "reason": routing["reason"], "changes_made": []}
    else:
        secrets = _scan_for_secrets(prompt)
        if secrets:
            return JSONResponse({
                "original": prompt,
                "optimized": prompt,
                "route": "pass",
                "score": score,
                "reason": "Possible secrets detected. Prompt not sent to optimization model.",
                "changes_made": [],
                "was_optimized": False,
                "tries_remaining": max(0, DEMO_LIMIT - current_count),
                "security_warning": True,
            })
        result = _optimize(prompt)
        was_optimized = result["optimized_prompt"] != prompt

    # Step 2: increment ONLY after successful processing
    new_count = await _upsert_usage(tracking_key, current_count)
    tries_remaining = max(0, DEMO_LIMIT - new_count)

    from analytics import track
    track("prompt_processed", user_id, {
        "route": route,
        "score": score,
        "was_optimized": was_optimized,
        "has_auth": user_id is not None,
        "secret_detected": False,
        "timed_out": False,
        "latency_ms": round((time.monotonic() - _t0) * 1000),
        "model": "haiku" if was_optimized else None,
        "tries_remaining": tries_remaining,
    })

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
        from analytics import track
        track("checkout_initiated", body.user_id, {
            "plan": body.plan,
            "user_id": body.user_id,
        })
        return JSONResponse({"checkout_url": session.url})
    except Exception:
        cid = getattr(request.state, "correlation_id", "-") if hasattr(request, "state") else "-"
        logger.exception("checkout session create failed", extra={"correlation_id": cid})
        raise HTTPException(status_code=500, detail="checkout_failed")


@app.post("/api/webhook")
async def stripe_webhook(request: Request) -> JSONResponse:
    cid = getattr(request.state, "correlation_id", "-")
    extra = {"correlation_id": cid}
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not STRIPE_WEBHOOK_SECRET:
        # Fail-closed: never accept unsigned webhooks. Without this guard, any
        # caller could forge a checkout.session.completed event and grant
        # themselves a paid subscription or trigger welcome emails to arbitrary
        # addresses (H-1).
        logger.error("rejected webhook: STRIPE_WEBHOOK_SECRET not configured", extra=extra)
        raise HTTPException(status_code=503, detail="webhook_not_configured")
    if not sig_header:
        raise HTTPException(status_code=400, detail="missing_signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        logger.warning("rejected webhook: invalid signature", extra=extra)
        raise HTTPException(status_code=400, detail="invalid_signature")
    except Exception:
        logger.exception("webhook parse error", extra=extra)
        raise HTTPException(status_code=400, detail="invalid_payload")

    if event["type"] == "checkout.session.completed":
        try:
            session = event["data"]["object"]
            metadata = session.metadata.to_dict() if session.metadata else {}
            user_id = metadata.get("user_id")
            plan = metadata.get("plan", "solo")
            customer_id = session.customer
            subscription_id = session.subscription
            customer_email = session.customer_details.email if session.customer_details else None

            # H-8: only act when metadata.user_id is present. customer_email is
            # used only for display / welcome mail — never as the upsert key.
            if not user_id:
                logger.warning("webhook checkout.session.completed without user_id metadata", extra=extra)
                return JSONResponse({"status": "ignored"})

            if SUPABASE_URL and SUPABASE_SECRET_KEY:
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
                            "subscription_started_at": datetime.now(timezone.utc).isoformat(),
                            "demo_tries_used": 0,
                        },
                        timeout=10,
                    )

            from analytics import track, identify
            identify(user_id, {"plan": plan})
            track("subscription_activated", user_id, {"plan": plan, "user_id": user_id})

            if customer_email:
                try:
                    await send_thankyou_email(customer_email, plan)
                except Exception:
                    logger.exception("welcome email failed", extra=extra)

        except Exception:
            logger.exception("webhook processing error", extra=extra)

    return JSONResponse({"status": "ok"})


@app.get("/api/verify-session")
async def verify_session(request: Request, session_id: str) -> JSONResponse:
    cid = getattr(request.state, "correlation_id", "-")
    extra = {"correlation_id": cid}
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="payments_not_configured")
    if not re.match(r"^cs_(?:test|live)_[A-Za-z0-9]{20,}$", session_id):
        raise HTTPException(status_code=400, detail="invalid_session_id")
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        return JSONResponse({
            "success": True,
            "plan": (session.metadata["plan"] if session.metadata and "plan" in session.metadata else "solo"),
            "email": (session.customer_details.email if session.customer_details and hasattr(session.customer_details, "email") else ""),
        })
    except stripe.error.StripeError:
        logger.exception("verify-session stripe error", extra=extra)
        raise HTTPException(status_code=400, detail="stripe_error")
    except Exception:
        logger.exception("verify-session unexpected error", extra=extra)
        raise HTTPException(status_code=500, detail="internal_error")


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "version": "0.1.9"})
