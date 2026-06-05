"""
Secret and sensitive pattern detection.
Run on every prompt before any cloud/model call.
Returns list of detected secret types. Empty list = safe to send.
"""
import re

_PATTERNS = [
    ("aws_access_key",      r"AKIA[0-9A-Z]{16}"),
    ("aws_secret_key",      r"[0-9a-zA-Z/+]{40}"),
    ("anthropic_api_key",   r"sk-ant-[a-zA-Z0-9\-_]{20,}"),
    ("openai_api_key",      r"sk-[a-zA-Z0-9]{48}"),
    ("github_token",        r"gh[pousr]_[A-Za-z0-9_]{36,}"),
    ("stripe_key",          r"[rs]k_(live|test)_[0-9a-zA-Z]{24,}"),
    ("private_key_header",  r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ("bearer_token",        r"Bearer\s+[A-Za-z0-9\-._~+/]+=*"),
    ("generic_api_key",     r"['\"]?api[_-]?key['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9\-_]{20,}"),
    ("password_pattern",    r"['\"]?password['\"]?\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
    ("connection_string",   r"(postgres|mysql|mongodb|redis)://[^\s]+:[^\s]+@"),
]

_COMPILED = [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _PATTERNS]

_REDACTION_MAP = {
    "aws_access_key":     "[REDACTED_AWS_ACCESS_KEY]",
    "aws_secret_key":     "[REDACTED_AWS_SECRET]",
    "anthropic_api_key":  "[REDACTED_ANTHROPIC_KEY]",
    "openai_api_key":     "[REDACTED_OPENAI_KEY]",
    "github_token":       "[REDACTED_GITHUB_TOKEN]",
    "stripe_key":         "[REDACTED_STRIPE_KEY]",
    "private_key_header": "[REDACTED_PRIVATE_KEY]",
    "bearer_token":       "[REDACTED_BEARER_TOKEN]",
    "generic_api_key":    "[REDACTED_API_KEY]",
    "password_pattern":   "[REDACTED_PASSWORD]",
    "connection_string":  "[REDACTED_CONNECTION_STRING]",
}


def scan_for_secrets(text: str) -> list[str]:
    """Return list of secret type names found in text. Empty = clean."""
    found = []
    for name, pattern in _COMPILED:
        if pattern.search(text):
            found.append(name)
    return found


def redact_secrets(text: str) -> tuple[str, list[str]]:
    """
    Replace detected secrets with redaction placeholders.
    Returns (redacted_text, list_of_redacted_types).
    """
    redacted = text
    found = []
    for name, pattern in _COMPILED:
        if pattern.search(redacted):
            redacted = pattern.sub(_REDACTION_MAP[name], redacted)
            found.append(name)
    return redacted, found
