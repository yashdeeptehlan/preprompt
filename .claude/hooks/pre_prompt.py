#!/usr/bin/env python3
"""
Claude Code UserPromptSubmit hook for PrePrompt.

Receives JSON on stdin, writes JSON to stdout.
  stdin:  {"prompt": "...", "conversation_history": [...], "turn_number": N}
  stdout: {"prompt": "..."}   ← optimized or original, never blocked

DB writes are done via JSON sidecar files in ~/.preprompt/pending/
so the hook never touches the SQLite file directly (avoids lock conflict
with the running MCP server). The MCP server flushes sidecars on the
next optimize_prompt call.
"""

import sys
import os
import json
import uuid
import time
from pathlib import Path


# ── Box annotation constants ──────────────────────────────────────────────────
_WIDTH  = 62          # total box width
_INNER  = _WIDTH - 4  # content between "║ " and " ║"  (58 chars)
_TEXT_W = 48          # prompt text width after label   (58 - 10)


def _box_line(content: str) -> str:
    return f"║ {content:<{_INNER}} ║"


def _render_annotation(score: int, reason: str, original: str, optimized: str) -> str:
    import textwrap

    # Header: ╔═ PrePrompt +{score} ══...══╗
    prefix = f"╔═ PrePrompt +{score} "
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


def _render_clarify_annotation(score: int, question: str, original: str) -> str:
    import textwrap
    prefix = f"╔═ PrePrompt CLARIFY "
    header = prefix + "═" * (_WIDTH - len(prefix) - 1) + "╗"
    sep = "╠" + "═" * (_WIDTH - 2) + "╣"
    q_wrapped = textwrap.wrap(f"? {question}", _INNER) or [question[:_INNER]]
    q_lines = [_box_line(line) for line in q_wrapped]
    orig_text = original if len(original) <= _TEXT_W else original[:_TEXT_W - 3] + "..."
    orig_line = _box_line(f"PROMPT    {orig_text}")
    info_line = _box_line("PrePrompt is asking for clarification first.")
    footer = "╚" + "═" * (_WIDTH - 2) + "╝"
    return "\n".join([header] + [info_line] + [sep] + q_lines + [sep, orig_line] + [footer])


def _log_activity(
    score: int,
    was_intercepted: bool,
    original: str,
    optimized: str,
    reason: str,
) -> None:
    """Append interception event to ~/.preprompt/activity.log."""
    import datetime
    log_path = Path.home() / ".preprompt" / "activity.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    if was_intercepted:
        line = (
            f"[{ts}] +{score} INTERCEPTED | {original[:60]}...\n"
            f"         → {optimized[:80]}...\n"
        )
    else:
        line = f"[{ts}] score={score} passthrough | {original[:60]}\n"
    with open(log_path, "a") as f:
        f.write(line)


def _write_sidecar(
    prompt: str,
    optimized: str,
    score: int,
    was_intercepted: bool,
    turn_number: int,
    route: str = "enrich",
) -> None:
    """Write event to ~/.preprompt/pending/<uuid>.json for async DB flush."""
    sidecar = {
        "original_prompt": prompt,
        "optimized_prompt": optimized,
        "classifier_score": score,
        "was_intercepted": was_intercepted,
        "turn_number": turn_number,
        "timestamp": time.time(),
        "route": route,
    }
    sidecar_dir = Path.home() / ".preprompt" / "pending"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = sidecar_dir / f"{uuid.uuid4()}.json"
    sidecar_path.write_text(json.dumps(sidecar))


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
        _PROJECT_ROOT = os.path.dirname(_CLAUDE_DIR)      # project root

        # Add project root to path so mcp_server imports work
        if _PROJECT_ROOT not in sys.path:
            sys.path.insert(0, _PROJECT_ROOT)

        # Load .env from project root
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

        # ── Route (no API call — always fast) ────────────────────────────────
        from mcp_server.classifier import route_prompt
        routing = route_prompt(prompt, history, turn)
        route = routing["route"]
        score = routing["quality_score"]

        if route == "pass":
            passthrough()
            sys.exit(0)

        if route == "clarify":
            question = routing.get("missing_context", [])
            _CLARIFY_TEMPLATES = {
                "target area": "What specifically should be improved: UI/UX, performance, code quality, accessibility, or architecture?",
                "desired outcome": "What should the end result look like?",
                "scope boundary": "Should this be a minimal targeted fix or a broader refactor?",
                "target file or component": "Which file, component, or function should this apply to?",
            }
            clarifying_q = None
            for ctx in question:
                if ctx in _CLARIFY_TEMPLATES:
                    clarifying_q = _CLARIFY_TEMPLATES[ctx]
                    break
            if not clarifying_q:
                clarifying_q = "What specifically do you want changed, and what should the result look like?"

            print(_render_clarify_annotation(score, clarifying_q, prompt), file=sys.stderr)

            clarified_prompt = (
                f"Before answering, ask the user this clarifying question and wait "
                f"for their response before proceeding:\n\n"
                f"\"{clarifying_q}\"\n\n"
                f"Original request: {prompt}"
            )

            try:
                _write_sidecar(prompt, prompt, score, False, turn, route="clarify")
            except Exception:
                pass

            try:
                _log_activity(score, False, prompt, prompt, f"[CLARIFY] {clarifying_q}")
            except Exception:
                pass

            print(json.dumps({"prompt": clarified_prompt}))
            sys.exit(0)

        # ── Check API key before importing optimizer (which triggers config) ──
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            print(
                "[PrePrompt WARNING] ANTHROPIC_API_KEY not set — skipping optimization",
                file=sys.stderr,
            )
            passthrough()
            sys.exit(0)

        # ── Optimize via Haiku API ────────────────────────────────────────────
        from mcp_server.optimizer import optimize

        result = optimize(prompt, history)
        optimized: str = result["optimized_prompt"]
        reason: str = result["reason"]
        was_intercepted: bool = optimized != prompt

        # ── Write sidecar (no DB lock needed) ────────────────────────────────
        try:
            _write_sidecar(prompt, optimized, score, was_intercepted, turn, route="enrich")
        except Exception as sidecar_err:
            print(f"[PrePrompt] Sidecar write failed: {sidecar_err}", file=sys.stderr)

        # ── Rich annotation to stderr ─────────────────────────────────────────
        if was_intercepted:
            print(_render_annotation(score, reason, prompt, optimized), file=sys.stderr)
        else:
            print(f"[PrePrompt +{score}] {reason}", file=sys.stderr)

        # ── Activity log (never blocks the hook) ──────────────────────────────
        try:
            _log_activity(score, was_intercepted, prompt, optimized, reason)
        except Exception:
            pass

        print(json.dumps({"prompt": optimized}))

    except Exception as e:
        print(f"[PrePrompt ERROR] {e}", file=sys.stderr)
        passthrough()

    sys.exit(0)


if __name__ == "__main__":
    main()
