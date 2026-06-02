"""MCP tool definitions exposed to Claude Code / Cursor."""

from mcp.server.fastmcp import FastMCP

from mcp_server.classifier import classify_prompt, route_prompt, OPTIMIZATION_THRESHOLD
from mcp_server.optimizer import optimize
from mcp_server.extractor import update_memory_from_prompt
from storage.db import (
    save_prompt_event,
    get_recent_history,
    get_or_create_session,
    flush_pending_hook_events,
)

mcp = FastMCP("PrePrompt")

_CLARIFY_TEMPLATES = {
    "target area": (
        "What specifically should be improved: UI/UX, performance, code quality, "
        "accessibility, or architecture?"
    ),
    "desired outcome": "What should the end result look like?",
    "scope boundary": "Should this be a minimal targeted fix or a broader refactor?",
    "target file or component": (
        "Which file, component, or function should this apply to?"
    ),
}


def _clarifying_question(missing_context: list) -> str:
    for ctx in missing_context:
        if ctx in _CLARIFY_TEMPLATES:
            return _CLARIFY_TEMPLATES[ctx]
    return "What specifically do you want changed, and what should the result look like?"

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
    routing = route_prompt(user_prompt, conversation_history, turn_number)
    route = routing["route"]
    score = routing["quality_score"]

    if route == "clarify":
        question = _clarifying_question(routing.get("missing_context", []))
        save_prompt_event(
            original_prompt=user_prompt,
            optimized_prompt=user_prompt,
            classifier_score=score,
            was_intercepted=False,
            turn_number=turn_number,
            session_id=_SESSION_ID,
            route="clarify",
        )
        update_memory_from_prompt(user_prompt, conversation_history)
        return {
            "optimized_prompt": user_prompt,
            "was_intercepted": False,
            "score": score,
            "route": "clarify",
            "reason": routing["reason"],
            "clarifying_question": question,
        }

    if route == "pass":
        save_prompt_event(
            original_prompt=user_prompt,
            optimized_prompt=user_prompt,
            classifier_score=score,
            was_intercepted=False,
            turn_number=turn_number,
            session_id=_SESSION_ID,
            route="pass",
        )
        update_memory_from_prompt(user_prompt, conversation_history)
        return {
            "optimized_prompt": user_prompt,
            "was_intercepted": False,
            "score": score,
            "route": "pass",
            "reason": routing["reason"],
        }

    # route == "enrich"
    result = optimize(user_prompt, conversation_history)
    optimized = result["optimized_prompt"]
    was_intercepted = optimized != user_prompt
    save_prompt_event(
        original_prompt=user_prompt,
        optimized_prompt=optimized,
        classifier_score=score,
        was_intercepted=was_intercepted,
        turn_number=turn_number,
        session_id=_SESSION_ID,
        route="enrich",
    )
    update_memory_from_prompt(user_prompt, conversation_history)
    return {
        "optimized_prompt": optimized,
        "was_intercepted": was_intercepted,
        "score": score,
        "route": "enrich",
        "reason": result["reason"],
    }


@mcp.tool()
def get_prompt_history(limit: int = 20) -> list[dict]:
    """Return the *limit* most recent prompt events for this session."""
    return get_recent_history(get_or_create_session(), limit)
