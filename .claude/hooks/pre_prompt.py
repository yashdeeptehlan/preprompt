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


def _write_sidecar(
    prompt: str,
    optimized: str,
    score: int,
    was_intercepted: bool,
    turn_number: int,
) -> None:
    """Write event to ~/.preprompt/pending/<uuid>.json for async DB flush."""
    sidecar = {
        "original_prompt": prompt,
        "optimized_prompt": optimized,
        "classifier_score": score,
        "was_intercepted": was_intercepted,
        "turn_number": turn_number,
        "timestamp": time.time(),
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
            _write_sidecar(prompt, optimized, score, was_intercepted, turn)
        except Exception as sidecar_err:
            print(f"[PrePrompt] Sidecar write failed: {sidecar_err}", file=sys.stderr)

        # ── Rich annotation to stderr ─────────────────────────────────────────
        if was_intercepted:
            print(_render_annotation(score, reason, prompt, optimized), file=sys.stderr)
        else:
            print(f"[PrePrompt +{score}] {reason}", file=sys.stderr)

        print(json.dumps({"prompt": optimized}))

    except Exception as e:
        print(f"[PrePrompt ERROR] {e}", file=sys.stderr)
        passthrough()

    sys.exit(0)


if __name__ == "__main__":
    main()
