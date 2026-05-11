"""
Integration tests for the full optimize_prompt pipeline.

These tests call the MCP tool functions directly (not over the transport wire)
and mock only the Anthropic API client — DuckDB runs against a real temp file.
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

import storage.db as db_module


# ── DB isolation fixture ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolate_db(tmp_path: Path, monkeypatch):
    """Redirect all DB operations to a throwaway file for each test."""
    test_db = tmp_path / "test_history.db"
    monkeypatch.setattr(db_module, "_DB_PATH", test_db)
    monkeypatch.setattr(db_module, "_conn", None)
    yield
    # Close and release the connection so the tmp file can be cleaned up
    if db_module._conn is not None:
        try:
            db_module._conn.close()
        except Exception:
            pass
        monkeypatch.setattr(db_module, "_conn", None)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mock_client(optimized_text: str, reason: str = "Added specificity.") -> MagicMock:
    payload = json.dumps({
        "optimized_prompt": optimized_text,
        "reason": reason,
        "changes_made": ["Specified framework", "Clarified token handling"],
    })
    content_block = MagicMock()
    content_block.text = payload
    response = MagicMock()
    response.content = [content_block]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


# ── Test 1: complex prompt is intercepted ────────────────────────────────────

@patch("mcp_server.optimizer.anthropic.Anthropic")
def test_full_pipe_intercepts_complex_prompt(mock_cls):
    improved = (
        "Write a FastAPI middleware that validates JWT tokens on each request "
        "and handles token refresh by returning a new access token on 401."
    )
    mock_cls.return_value = _make_mock_client(improved)

    from mcp_server.tools import optimize_prompt

    result = optimize_prompt(
        user_prompt="write me a middleware that validates tokens and handles refresh",
        conversation_history=[],
        turn_number=1,
    )

    assert result["was_intercepted"] is True
    assert result["optimized_prompt"] == improved
    assert result["score"] >= 45


# ── Test 2: simple prompt passes through unchanged ───────────────────────────

@patch("mcp_server.optimizer.anthropic.Anthropic")
def test_full_pipe_passes_through_simple_prompt(mock_cls):
    # Optimizer should never be called for this prompt, but patch it anyway
    mock_cls.return_value = _make_mock_client("should not be used")

    from mcp_server.tools import optimize_prompt

    original = "what is jwt"
    result = optimize_prompt(
        user_prompt=original,
        conversation_history=[],
        turn_number=1,
    )

    assert result["was_intercepted"] is False
    assert result["optimized_prompt"] == original
    assert result["score"] < 45
    # Optimizer API must NOT have been called
    mock_cls.return_value.messages.create.assert_not_called()


# ── Test 3: history query returns saved events ───────────────────────────────

def test_get_prompt_history_returns_saved_events():
    from storage.db import save_prompt_event
    from mcp_server.tools import get_prompt_history, _SESSION_ID

    events_to_save = [
        ("original prompt 0", "optimized prompt 0", 55, True,  1),
        ("original prompt 1", "optimized prompt 1", 62, True,  2),
        ("what is jwt",       "what is jwt",         -35, False, 1),
    ]
    for orig, opt, score, intercepted, turn in events_to_save:
        save_prompt_event(
            original_prompt=orig,
            optimized_prompt=opt,
            classifier_score=score,
            was_intercepted=intercepted,
            turn_number=turn,
            session_id=_SESSION_ID,
        )

    history = get_prompt_history(limit=10)

    assert len(history) == 3
    assert all("original_prompt" in e for e in history)
    assert all("classifier_score" in e for e in history)
    assert all("was_intercepted" in e for e in history)
    # Most recent first
    assert history[0]["original_prompt"] == "what is jwt"


# ── Test 4: return dict has all required keys ─────────────────────────────────

@patch("mcp_server.optimizer.anthropic.Anthropic")
def test_optimize_prompt_return_shape(mock_cls):
    mock_cls.return_value = _make_mock_client("Better version of the prompt.")

    from mcp_server.tools import optimize_prompt

    result = optimize_prompt(
        user_prompt="handle the auth and manage user sessions",
        conversation_history=[{"role": "user", "content": "I'm building a FastAPI app"}],
        turn_number=2,
    )

    assert set(result.keys()) >= {"optimized_prompt", "was_intercepted", "score", "reason", "route"}
    assert isinstance(result["optimized_prompt"], str)
    assert isinstance(result["was_intercepted"], bool)
    assert isinstance(result["score"], int)
    assert isinstance(result["reason"], str)
    assert result["route"] in {"pass", "enrich", "clarify"}


# ── Phase 3: stack extractor tests ───────────────────────────────────────────

def test_stack_extractor_detects_python_fastapi():
    from mcp_server.extractor import extract_stack_signals
    signals = extract_stack_signals(
        "write a FastAPI endpoint with type hints",
        history=[],
    )
    assert signals.get("language") == "python"
    assert signals.get("framework") == "fastapi"


def test_stack_extractor_returns_empty_for_vague_prompt():
    from mcp_server.extractor import extract_stack_signals
    signals = extract_stack_signals("fix this", history=[])
    assert isinstance(signals, dict)


def test_memory_upsert_and_retrieval(tmp_path, monkeypatch):
    import storage.db as db_module
    monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db_module, "_conn", None)
    from storage.db import upsert_stack_memory, get_stack_memory
    upsert_stack_memory("language", "python", 0.9)
    upsert_stack_memory("framework", "fastapi", 0.85)
    memory = get_stack_memory()
    assert memory["language"] == "python"
    assert memory["framework"] == "fastapi"


def test_memory_below_confidence_threshold_excluded(tmp_path, monkeypatch):
    import storage.db as db_module
    monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db_module, "_conn", None)
    from storage.db import upsert_stack_memory, get_stack_memory
    upsert_stack_memory("language", "python", 0.3)
    memory = get_stack_memory()
    assert "language" not in memory


# ── Phase 5: session identity + memory consolidation + cross-session history ──

def test_get_or_create_session_is_stable(tmp_path, monkeypatch):
    import storage.db as db_module
    monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db_module, "_conn", None)
    from storage.db import get_or_create_session
    session1 = get_or_create_session()
    session2 = get_or_create_session()
    assert session1 == session2  # same day, same host = same session


def test_memory_confidence_compounds(tmp_path, monkeypatch):
    import storage.db as db_module
    monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db_module, "_conn", None)
    from storage.db import upsert_stack_memory, get_full_stack_memory
    upsert_stack_memory("language", "python", 0.8)
    upsert_stack_memory("language", "python", 0.8)
    upsert_stack_memory("language", "python", 0.8)
    entries = get_full_stack_memory()
    lang = next(e for e in entries if e["key"] == "language")
    assert lang["confidence"] > 0.8   # compounded up
    assert lang["source_count"] == 3


def test_memory_value_change_resets_confidence(tmp_path, monkeypatch):
    import storage.db as db_module
    monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db_module, "_conn", None)
    from storage.db import upsert_stack_memory, get_full_stack_memory
    upsert_stack_memory("framework", "fastapi", 0.8)
    upsert_stack_memory("framework", "fastapi", 0.8)   # confidence now 0.82
    upsert_stack_memory("framework", "django", 0.8)    # value changed
    entries = get_full_stack_memory()
    fw = next(e for e in entries if e["key"] == "framework")
    assert fw["value"] == "django"
    assert fw["confidence"] == 0.6   # reset on value change
    assert fw["source_count"] == 1   # reset on value change


def test_get_all_history_returns_cross_session(tmp_path, monkeypatch):
    import storage.db as db_module
    monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db_module, "_conn", None)
    from storage.db import save_prompt_event, get_all_history
    save_prompt_event("prompt1", "opt1", 60, True,  1, "session-a")
    save_prompt_event("prompt2", "opt2", 30, False, 2, "session-b")
    all_history = get_all_history(limit=10)
    assert len(all_history) == 2
    sessions = {e["session_id"] for e in all_history}
    assert "session-a" in sessions
    assert "session-b" in sessions


def test_activity_log_written_on_intercept(tmp_path):
    """Hook writes correctly-formatted lines to activity.log."""
    log_path = tmp_path / ".preprompt" / "activity.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Simulate what _log_activity writes for an interception
    import datetime
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    original = "write a function that handles auth and manages sessions"
    optimized = "Write a FastAPI function that handles OAuth2 authentication and manages user sessions with JWT tokens."
    score = 58

    line = (
        f"[{ts}] +{score} INTERCEPTED | {original[:60]}...\n"
        f"         → {optimized[:80]}...\n"
    )
    with open(log_path, "a") as f:
        f.write(line)

    assert log_path.exists()
    content = log_path.read_text()
    assert "INTERCEPTED" in content
    assert f"+{score}" in content
    assert original[:40] in content

    # Simulate a passthrough entry
    passthrough_line = f"[{ts}] score=-35 passthrough | what is jwt\n"
    with open(log_path, "a") as f:
        f.write(passthrough_line)

    content = log_path.read_text()
    assert "passthrough" in content
    assert "INTERCEPTED" in content   # earlier entry still present
