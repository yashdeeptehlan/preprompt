#!/usr/bin/env python3
"""
Claude Code UserPromptSubmit hook for PrePrompt — pip-installable version.

Run as: python -m cli.hook

Reads JSON on stdin, writes JSON to stdout.
  stdin:  {"prompt": "...", "conversation_history": [...], "turn_number": N}
  stdout: {"prompt": "..."}   ← optimized or original, never blocked

Loads API key from ~/.preprompt/.env (works for pip-installed users).
mcp_server and storage are installed packages — no sys.path manipulation needed.
"""

import sys
import os
import json
import uuid
import time
from pathlib import Path


# ── Box annotation constants ──────────────────────────────────────────────────
_WIDTH  = 62
_INNER  = _WIDTH - 4
_TEXT_W = 48


def _box_line(content: str) -> str:
    return f"║ {content:<{_INNER}} ║"


def _render_annotation(score: int, reason: str, original: str, optimized: str) -> str:
    import textwrap
    prefix = f"╔═ PrePrompt +{score} "
    header = prefix + "═" * (_WIDTH - len(prefix) - 1) + "╗"
    reason_lines = [_box_line(l) for l in (textwrap.wrap(reason, _INNER) or [""])]
    sep = "╠" + "═" * (_WIDTH - 2) + "╣"
    orig_text = original if len(original) <= _TEXT_W else original[:_TEXT_W - 3] + "..."
    orig_line = _box_line(f"ORIGINAL  {orig_text}")
    opt_wrapped = textwrap.wrap(optimized, _TEXT_W) or [optimized[:_TEXT_W]]
    opt_lines = [_box_line(f"OPTIMIZED {opt_wrapped[0]}")] + [
        _box_line(f"          {line}") for line in opt_wrapped[1:]
    ]
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


def _log_activity(score: int, was_intercepted: bool, original: str, optimized: str, reason: str) -> None:
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


def _write_sidecar(prompt: str, optimized: str, score: int, was_intercepted: bool, turn_number: int, route: str = "enrich") -> None:
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
        # Load API key from ~/.preprompt/.env — works for pip-installed users
        from dotenv import load_dotenv
        load_dotenv(Path.home() / ".preprompt" / ".env")

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

        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            print(
                "[PrePrompt WARNING] ANTHROPIC_API_KEY not set — skipping optimization",
                file=sys.stderr,
            )
            passthrough()
            sys.exit(0)

        from mcp_server.optimizer import optimize

        result = optimize(prompt, history)
        optimized: str = result["optimized_prompt"]
        reason: str = result["reason"]
        was_intercepted: bool = optimized != prompt

        try:
            _write_sidecar(prompt, optimized, score, was_intercepted, turn, route="enrich")
        except Exception as sidecar_err:
            print(f"[PrePrompt] Sidecar write failed: {sidecar_err}", file=sys.stderr)

        if was_intercepted:
            print(_render_annotation(score, reason, prompt, optimized), file=sys.stderr)
        else:
            print(f"[PrePrompt +{score}] {reason}", file=sys.stderr)

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
