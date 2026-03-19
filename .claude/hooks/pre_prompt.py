#!/usr/bin/env python3
"""
Claude Code UserPromptSubmit hook for PromptForge.

Receives JSON on stdin, writes JSON to stdout.
  stdin:  {"prompt": "...", "conversation_history": [...], "turn_number": N}
  stdout: {"prompt": "..."}   ← optimized or original, never blocked
"""

import sys
import os
import json
import time


# ── Retry helper for DB writes under lock contention ─────────────────────────

def _save_with_retry(fn, *args, max_retries=3, delay=0.1):
    for attempt in range(max_retries):
        try:
            fn(*args)
            return
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(delay * (2 ** attempt))


# ── Box annotation constants ──────────────────────────────────────────────────
_WIDTH  = 62          # total box width
_INNER  = _WIDTH - 4  # content between "║ " and " ║"  (58 chars)
_TEXT_W = 48          # prompt text width after label   (58 - 10)


def _box_line(content: str) -> str:
    return f"║ {content:<{_INNER}} ║"


def _render_annotation(score: int, reason: str, original: str, optimized: str) -> str:
    import textwrap

    # Header: ╔═ PromptForge +{score} ══...══╗
    prefix = f"╔═ PromptForge +{score} "
    header = prefix + "═" * (_WIDTH - len(prefix) - 1) + "╗"

    # Reason (wrapped to inner width)
    reason_lines = [_box_line(l) for l in (textwrap.wrap(reason, _INNER) or [""])]

    # Separator
    sep = "╠" + "═" * (_WIDTH - 2) + "╣"

    # ORIGINAL (truncated to TEXT_W)
    orig_text = original if len(original) <= _TEXT_W else original[:_TEXT_W - 3] + "..."
    orig_line = _box_line(f"ORIGINAL  {orig_text}")

    # OPTIMIZED (wrapped, continuation indented 10 spaces to align with text)
    opt_wrapped = textwrap.wrap(optimized, _TEXT_W) or [optimized[:_TEXT_W]]
    opt_lines = [_box_line(f"OPTIMIZED {opt_wrapped[0]}")] + [
        _box_line(f"          {line}") for line in opt_wrapped[1:]
    ]

    # Footer
    footer = "╚" + "═" * (_WIDTH - 2) + "╝"

    return "\n".join([header] + reason_lines + [sep, orig_line] + opt_lines + [footer])


def main() -> None:
    raw = sys.stdin.read()

    # Parse stdin — on failure, echo back whatever we got and exit cleanly
    try:
        data = json.loads(raw)
    except Exception:
        print(raw, end="")
        sys.exit(0)

    prompt: str = data.get("prompt", "")
    history: list = data.get("conversation_history", [])
    turn: int = data.get("turn_number", 1)

    def passthrough() -> None:
        print(json.dumps({"prompt": prompt}))

    try:
        # Always resolve paths relative to this file's location,
        # not the caller's working directory
        _HOOK_FILE = os.path.abspath(__file__)
        _HOOK_DIR = os.path.dirname(_HOOK_FILE)          # .claude/hooks/
        _CLAUDE_DIR = os.path.dirname(_HOOK_DIR)          # .claude/
        _PROJECT_ROOT = os.path.dirname(_CLAUDE_DIR)      # promptforge/

        # Add project root to path so mcp_server + storage imports work
        if _PROJECT_ROOT not in sys.path:
            sys.path.insert(0, _PROJECT_ROOT)

        # Force fresh connection in hook process (avoid inheriting MCP server's lock)
        import storage.db as _db_module
        _db_module._conn = None

        # Load .env from project root
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

        # ── Classify (no API call — always fast) ──────────────────────────────
        from mcp_server.classifier import classify_prompt, OPTIMIZATION_THRESHOLD

        score = classify_prompt(prompt, history, turn)

        if score < OPTIMIZATION_THRESHOLD:
            passthrough()
            sys.exit(0)

        # ── Check API key before importing optimizer (which triggers config) ──
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            print(
                "[PromptForge WARNING] ANTHROPIC_API_KEY not set — skipping optimization",
                file=sys.stderr,
            )
            passthrough()
            sys.exit(0)

        # ── Optimize via Haiku API ────────────────────────────────────────────
        from mcp_server.optimizer import optimize
        from storage.db import save_prompt_event, get_or_create_session

        result = optimize(prompt, history)
        optimized: str = result["optimized_prompt"]
        reason: str = result["reason"]
        was_intercepted: bool = optimized != prompt

        session_id = get_or_create_session()

        _save_with_retry(
            save_prompt_event,
            prompt,
            optimized,
            score,
            was_intercepted,
            turn,
            session_id,
        )

        # ── Update stack memory (failure must never block the hook) ───────────
        try:
            from mcp_server.extractor import update_memory_from_prompt
            _save_with_retry(update_memory_from_prompt, prompt, history)
        except Exception as mem_err:
            print(f"[PromptForge] Memory update failed: {mem_err}", file=sys.stderr)

        # ── Rich annotation to stderr ─────────────────────────────────────────
        if was_intercepted:
            print(_render_annotation(score, reason, prompt, optimized), file=sys.stderr)
        else:
            print(f"[PromptForge +{score}] {reason}", file=sys.stderr)

        print(json.dumps({"prompt": optimized}))

    except Exception as e:
        print(f"[PromptForge ERROR] {e}", file=sys.stderr)
        passthrough()

    sys.exit(0)


if __name__ == "__main__":
    main()
