"""
Secret and sensitive pattern detection.
Run on every prompt before any cloud/model call.
Returns list of detected secret types. Empty list = safe to send.

Pattern notes (audit M-1):
- aws_secret_key requires nearby "aws"/"secret" context. The bare 40-char
  base64 pattern produced false positives on JWT bodies and hashes.
- openai_api_key matches the modern 48+ char prefixed format (sk-/sk-proj-/...).
- bearer_token requires >=20 char tokens to avoid matching the literal word
  "Bearer foo" in docs.
- generic_api_key and password_pattern require quoted values so identifier
  substrings like "compass" or "password_field" don't trip the scanner.
"""
import re

_PATTERNS = [
    ("aws_access_key",      r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("aws_secret_key",      r"(?is)aws[A-Za-z0-9_\-]*secret[^\n]{0,60}?\b[A-Za-z0-9/+]{40}\b"),
    ("anthropic_api_key",   r"\bsk-ant-(?:api|admin)[A-Za-z0-9_\-]{20,}\b"),
    ("openai_api_key",      r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{40,}\b"),
    ("github_token",        r"\bgh[pousr]_[A-Za-z0-9_]{36,}\b"),
    ("stripe_key",          r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{24,}\b"),
    ("private_key_header",  r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    ("bearer_token",        r"\b[Bb]earer\s+[A-Za-z0-9\-._~+/]{20,}=*"),
    ("generic_api_key",     r"(?i)\bapi[_-]?key\b\s*[:=]\s*['\"][A-Za-z0-9\-_]{20,}['\"]"),
    ("password_pattern",    r"(?i)(?<![A-Za-z])password\s*[:=]\s*['\"][^\s'\"]{8,}['\"]"),
    ("connection_string",   r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:@]+:[^\s:@]+@\S+"),
]

_COMPILED = [(name, re.compile(pattern)) for name, pattern in _PATTERNS]

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
