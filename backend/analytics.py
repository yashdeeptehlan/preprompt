"""PostHog analytics helper. Never raises — analytics must not break the product.

Failures are surfaced via the ``preprompt.analytics`` logger so they aren't
silently swallowed (L-4 in the audit). Operators can route this logger to
Sentry/structured logs without touching application code.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger("preprompt.analytics")

_posthog = None


def _get_posthog():
    global _posthog
    if _posthog is None:
        try:
            import posthog as ph
            ph.api_key = os.environ.get("POSTHOG_API_KEY", "")
            ph.host = "https://app.posthog.com"
            ph.debug = False
            _posthog = ph
        except Exception:
            logger.exception("posthog import failed; analytics disabled")
    return _posthog


def track(event: str, user_id: Optional[str], properties: dict) -> None:
    try:
        ph = _get_posthog()
        if ph and ph.api_key:
            ph.capture(
                distinct_id=user_id or "anonymous",
                event=event,
                properties={**properties, "$lib": "preprompt-backend"},
            )
    except Exception:
        logger.warning("posthog capture failed for event=%s", event, exc_info=True)


def identify(user_id: str, properties: dict) -> None:
    try:
        ph = _get_posthog()
        if ph and ph.api_key:
            ph.identify(distinct_id=user_id, properties=properties)
    except Exception:
        logger.warning("posthog identify failed for user=%s", user_id, exc_info=True)


def flush() -> None:
    """Force-send queued events. Required in short-lived processes (CLI, hook)."""
    try:
        ph = _get_posthog()
        if ph and ph.api_key:
            ph.flush()
    except Exception:
        logger.warning("posthog flush failed", exc_info=True)


def track_event(event: str, properties: Optional[dict] = None, user_id: Optional[str] = None) -> None:
    """Best-effort fire-and-flush. Safe to call from short-lived processes.

    No-ops if POSTHOG_API_KEY is unset, so callers don't need to guard.
    """
    if not os.environ.get("POSTHOG_API_KEY", "").strip():
        return
    track(event, user_id, properties or {})
    flush()
