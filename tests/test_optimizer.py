"""Tests for the prompt optimizer."""

import json
import time
from unittest.mock import MagicMock, patch


def _mock_api_response(optimized: str, reason: str, changes: list) -> MagicMock:
    payload = json.dumps({
        "optimized_prompt": optimized,
        "reason": reason,
        "changes_made": changes,
    })
    msg = MagicMock()
    msg.content = [MagicMock(text=payload)]
    return msg


@patch("mcp_server.optimizer.anthropic.Anthropic")
def test_optimize_returns_expected_keys(mock_anthropic_cls):
    from mcp_server.optimizer import optimize
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_api_response(
        "Write a FastAPI middleware that validates JWT tokens and handles token refresh.",
        "Added framework and token type specificity.",
        ["Specified FastAPI", "Specified JWT", "Clarified token refresh behavior"],
    )
    result = optimize("write me a middleware that validates tokens and handles refresh", [])
    assert "optimized_prompt" in result
    assert "reason" in result
    assert "changes_made" in result
    assert isinstance(result["changes_made"], list)


@patch("mcp_server.optimizer.anthropic.Anthropic")
def test_optimize_uses_history_in_call(mock_anthropic_cls):
    from mcp_server.optimizer import optimize
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _mock_api_response("improved", "clearer", [])

    history = [
        {"role": "user", "content": "I'm building a FastAPI app"},
        {"role": "assistant", "content": "Sure, let's get started."},
    ]
    optimize("add auth", history)

    call_args = mock_client.messages.create.call_args
    messages = call_args.kwargs.get("messages") or call_args.args[0]
    # History content should appear somewhere in the request
    user_content = str(messages)
    assert "FastAPI" in user_content


@patch("mcp_server.optimizer.anthropic.Anthropic")
def test_optimize_graceful_fallback_on_api_error(mock_anthropic_cls):
    from mcp_server.optimizer import optimize
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.side_effect = Exception("API error")

    original = "write me a middleware"
    result = optimize(original, [])
    assert result["optimized_prompt"] == original
    assert result["changes_made"] == []


@patch("mcp_server.optimizer.anthropic.Anthropic")
def test_optimize_graceful_fallback_on_bad_json(mock_anthropic_cls):
    from mcp_server.optimizer import optimize
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    bad_msg = MagicMock()
    bad_msg.content = [MagicMock(text="not valid json {{")]
    mock_client.messages.create.return_value = bad_msg

    original = "do something"
    result = optimize(original, [])
    assert result["optimized_prompt"] == original


# ── _optimize_with_timeout tests ──────────────────────────────────────────────

def test_optimize_with_timeout_times_out():
    from cli.hook import _optimize_with_timeout

    def slow_optimize(p, h):
        time.sleep(3)
        return {"optimized_prompt": "never returned", "reason": "", "changes_made": []}

    start = time.time()
    result = _optimize_with_timeout(slow_optimize, "my prompt", [], timeout=2.0)
    elapsed = time.time() - start

    assert elapsed < 2.5
    assert result["optimized_prompt"] == "my prompt"
    assert result.get("timed_out") is True


def test_optimize_with_timeout_on_exception():
    from cli.hook import _optimize_with_timeout

    def failing_optimize(p, h):
        raise ValueError("API unavailable")

    result = _optimize_with_timeout(failing_optimize, "my prompt", [], timeout=2.0)

    assert result["optimized_prompt"] == "my prompt"
    assert result.get("error") is True
    assert "timed_out" not in result


def test_optimize_with_timeout_returns_result_on_success():
    from cli.hook import _optimize_with_timeout

    def good_optimize(p, h):
        return {
            "optimized_prompt": "improved prompt",
            "reason": "added specificity",
            "changes_made": ["added output format"],
        }

    result = _optimize_with_timeout(good_optimize, "my prompt", [], timeout=2.0)

    assert result["optimized_prompt"] == "improved prompt"
    assert result["reason"] == "added specificity"
    assert result["changes_made"] == ["added output format"]
    assert "timed_out" not in result
    assert "error" not in result
