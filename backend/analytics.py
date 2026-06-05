"""PostHog analytics helper. Never raises — analytics must not break the product."""

import os
from typing import Optional

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
            pass
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
        pass


def identify(user_id: str, properties: dict) -> None:
    try:
        ph = _get_posthog()
        if ph and ph.api_key:
            ph.identify(distinct_id=user_id, properties=properties)
    except Exception:
        pass
