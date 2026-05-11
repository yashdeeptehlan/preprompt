"""Prompt optimizer — rewrites a prompt using Haiku with conversation context."""

import json
import anthropic
from mcp_server.config import settings
from storage.db import get_stack_memory

_MODEL = getattr(settings, "preprompt_model", None) or "claude-haiku-4-5-20251001"

_SYSTEM = """\
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
4. Do NOT assume target files, components, or frameworks unless present in history
   or stack memory.
5. For bug-fix prompts: default to "smallest safe fix" — add this constraint
   explicitly unless the user asked for a broader change.
6. For bug-fix prompts: add "do not change unrelated files" unless user said otherwise.
7. If you must add an assumption to make the prompt executable, label it explicitly
   in changes_made as "Assumption added: <what you assumed>".
8. Never make the prompt sound more ambitious than the user intended.

WHAT YOU MAY DO:
- Add output format expectations (e.g. "explain what changed")
- Add verification steps (e.g. "describe how to test the fix")
- Add scope boundaries (e.g. "do not refactor unrelated code")
- Add specificity from context already present in history or stack memory
- Improve structure and clarity without changing meaning

You will receive:
  • The original prompt
  • Recent conversation history so you understand the user's stack, domain, and intent
  • Known stack memory from past sessions

Return a JSON object with exactly these keys:
  "optimized_prompt" : the rewritten prompt (string)
  "reason"           : one sentence explaining the main improvement (string)
  "changes_made"     : list of short strings, each describing one specific change
                       (prefix assumption entries with "Assumption added: ")

Respond ONLY with valid JSON. No markdown fences, no extra commentary.\
"""


def optimize(prompt: str, history: list) -> dict:
    """Rewrite *prompt* using conversation *history* and learned stack context.

    Always returns a dict with keys: optimized_prompt, reason, changes_made.
    Falls back to the original prompt on any error.
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    # ── Inject learned stack memory into the system prompt ────────────────────
    try:
        stack_memory = get_stack_memory()
    except Exception:
        stack_memory = {}

    memory_context = ""
    if stack_memory:
        lines = [f"  - {k}: {v}" for k, v in stack_memory.items()]
        memory_context = (
            "\n\nUser's known stack (learned from past sessions):\n"
            + "\n".join(lines)
            + "\n\nUse this context when rewriting the prompt — inject the "
              "correct language/framework/style even if not stated explicitly.\n"
        )

    # ── Build conversation history block ─────────────────────────────────────
    history_text = ""
    if history:
        recent = history[-6:]
        history_text = "\n".join(
            f"{turn.get('role', 'user').upper()}: {turn.get('content', '')}"
            for turn in recent
        )
        history_text = f"\n\nConversation history:\n{history_text}\n"

    user_message = f"Original prompt:{history_text}\n{prompt}"

    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=512,
            system=_SYSTEM + memory_context,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown code fences if the model wraps its JSON response
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]          # drop opening fence + tag
            raw = raw[raw.find("\n") + 1:]         # drop the "json" line
            raw = raw.rsplit("```", 1)[0].strip()  # drop closing fence
        data = json.loads(raw)
        return {
            "optimized_prompt": data.get("optimized_prompt", prompt),
            "reason": data.get("reason", ""),
            "changes_made": data.get("changes_made", []),
        }
    except Exception:
        return {
            "optimized_prompt": prompt,
            "reason": "Optimization unavailable; original prompt returned.",
            "changes_made": [],
        }
