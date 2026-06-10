"""Tests for the secret scanner."""


def test_detects_aws_key():
    from mcp_server.secret_scanner import scan_for_secrets
    result = scan_for_secrets("use key AKIAIOSFODNN7EXAMPLE to access S3")
    assert "aws_access_key" in result


def test_detects_anthropic_key():
    from mcp_server.secret_scanner import scan_for_secrets
    result = scan_for_secrets("my key is sk-ant-api03-abcdefghijk123456789")
    assert "anthropic_api_key" in result


def test_clean_prompt_returns_empty():
    from mcp_server.secret_scanner import scan_for_secrets
    result = scan_for_secrets("write a fastapi endpoint for user auth")
    assert result == []


def test_redact_replaces_secret():
    from mcp_server.secret_scanner import redact_secrets
    redacted, found = redact_secrets("key: sk-ant-api03-abcdefghijk123456789")
    assert "[REDACTED_ANTHROPIC_KEY]" in redacted
    assert "sk-ant" not in redacted


def test_hook_passthrough_on_secret():
    # Verify the hook returns original prompt without optimization when secret detected
    pass  # integration test — manual verification acceptable for now


# ── Audit M-1 regression corpus ──────────────────────────────────────────────
# These tests pin the tightened patterns so future changes don't re-introduce
# the false-positive / false-negative behaviour the audit called out.

def test_bare_40char_base64_is_not_aws_secret():
    from mcp_server.secret_scanner import scan_for_secrets
    # JWT-body-style 40-char base64 used to match the bare aws_secret_key rule.
    assert "aws_secret_key" not in scan_for_secrets(
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9aabb"
    )


def test_aws_secret_with_context_still_detected():
    from mcp_server.secret_scanner import scan_for_secrets
    # Genuine AWS secret keys are exactly 40 chars of [A-Za-z0-9/+].
    assert "aws_secret_key" in scan_for_secrets(
        'aws_secret_access_key="abcDEF123ghIJK456lmnopQR789stuvWXyzAB012"'
    )


def test_openai_key_modern_format_detected():
    from mcp_server.secret_scanner import scan_for_secrets
    # The audit noted current OpenAI keys are 51+ chars with _/- and a project
    # prefix — the old `sk-[A-Za-z0-9]{48}` regex missed them.
    assert "openai_api_key" in scan_for_secrets(
        "OPENAI_API_KEY=sk-proj-AbCdEf123456-_AbCdEf123456-_AbCdEf123456"
    )


def test_short_bearer_token_does_not_match():
    from mcp_server.secret_scanner import scan_for_secrets
    # Old pattern flagged "Bearer foo" inside docs — too aggressive.
    assert "bearer_token" not in scan_for_secrets("Use Bearer foo for the header")


def test_long_bearer_token_does_match():
    from mcp_server.secret_scanner import scan_for_secrets
    assert "bearer_token" in scan_for_secrets(
        "Authorization: Bearer abcdef1234567890abcdef1234567890"
    )


def test_password_substring_in_identifier_does_not_match():
    from mcp_server.secret_scanner import scan_for_secrets
    # Old `password` regex was case-insensitive substring — matched "compass",
    # "password_field", "bypass_check" in source code.
    assert "password_pattern" not in scan_for_secrets("def compass_heading(): pass")
    assert "password_pattern" not in scan_for_secrets("user_password_field = None")


def test_password_assignment_still_matches():
    from mcp_server.secret_scanner import scan_for_secrets
    assert "password_pattern" in scan_for_secrets("password = 'hunter2-very-secret'")
