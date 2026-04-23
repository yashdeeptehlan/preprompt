"""MCP tool definitions exposed to Claude Code / Cursor."""

from mcp.server.fastmcp import FastMCP

from mcp_server.classifier import classify_prompt, OPTIMIZATION_THRESHOLD
from mcp_server.optimizer import optimize
from mcp_server.extractor import update_memory_from_prompt
from storage.db import (
    save_prompt_event,
    get_recent_history,
    get_or_create_session,
    flush_pending_hook_events,
)

mcp = FastMCP("PrePrompt")

# Stable session identity: one session per hostname per calendar day.
# Kept as a module-level alias so existing tests can import _SESSION_ID.
_SESSION_ID = get_or_create_session()


@mcp.tool()
def optimize_prompt(
    user_prompt: str,
    conversation_history: list[dict],
    turn_number: int,
) -> dict:
    """Intercept a prompt, score it, optionally rewrite it, and log the event.

    Parameters
    ----------
    user_prompt:
        The raw prompt the user is about to send to the LLM.
    conversation_history:
        Previous turns as a list of {role, content} dicts.
    turn_number:
        Which turn in the conversation this prompt belongs to (1-based).

    Returns
    -------
    dict with keys:
      optimized_prompt : str   — best version of the prompt
      was_intercepted  : bool  — True if the optimizer rewrote it
      score            : int   — classifier score (0–100)
      reason           : str   — brief explanation of what changed (or why not)
    """
    flush_pending_hook_events()
    score = classify_prompt(user_prompt, conversation_history, turn_number)

    if score >= OPTIMIZATION_THRESHOLD:
        result = optimize(user_prompt, conversation_history)
        optimized = result["optimized_prompt"]
        was_intercepted = optimized != user_prompt
        reason = result["reason"]
    else:
        optimized = user_prompt
        was_intercepted = False
        reason = f"Score {score} below threshold {OPTIMIZATION_THRESHOLD}; prompt is clear enough."

    save_prompt_event(
        original_prompt=user_prompt,
        optimized_prompt=optimized,
        classifier_score=score,
        was_intercepted=was_intercepted,
        turn_number=turn_number,
        session_id=get_or_create_session(),
    )

    update_memory_from_prompt(user_prompt, conversation_history)

    return {
        "optimized_prompt": optimized,
        "was_intercepted": was_intercepted,
        "score": score,
        "reason": reason,
    }


@mcp.tool()
def get_prompt_history(limit: int = 20) -> list[dict]:
    """Return the *limit* most recent prompt events for this session."""
    return get_recent_history(get_or_create_session(), limit)
