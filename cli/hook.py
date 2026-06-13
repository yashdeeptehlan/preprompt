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
import inspect
import concurrent.futures
import threading
from pathlib import Path


# Bounded executor — at most one optimize call runs at a time. On timeout we
# replace the executor so a stuck worker does not pile up on subsequent calls
# (audit M-10). The previous design created a fresh ThreadPoolExecutor on each
# call and called shutdown(wait=False) on timeout, leaving the worker thread
# alive until Anthropic eventually replied — a slow leak in long-lived MCP
# sessions. We pair the bounded executor with httpx's native socket timeout
# inside optimize() so the worker exits promptly on real network calls.

_executor_lock = threading.Lock()
_executor: concurrent.futures.ThreadPoolExecutor | None = None


def _get_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="preprompt-optimize",
            )
        return _executor


def _reset_executor() -> None:
    """Drop the current executor so the timed-out worker can't pin the slot."""
    global _executor
    with _executor_lock:
        stale = _executor
        _executor = None
    if stale is not None:
        stale.shutdown(wait=False)


def _optimize_with_timeout(optimize_fn, prompt: str, history: list, timeout: float = 2.0) -> dict:
    """Call optimize_fn(prompt, history[, timeout]) with a hard wall-clock cap.

    The cap is enforced via a bounded ThreadPoolExecutor (max_workers=1). If
    the call exceeds the budget we recycle the executor so the stuck worker is
    abandoned without piling up future workers. ``timeout`` is also forwarded
    to optimize_fn when the function accepts it so httpx can cancel the
    underlying socket on its own clock.
    """
    accepts_timeout = False
    try:
        accepts_timeout = "timeout" in inspect.signature(optimize_fn).parameters
    except (TypeError, ValueError):
        pass

    def _runner() -> dict:
        if accepts_timeout:
            return optimize_fn(prompt, history, timeout=timeout)
        return optimize_fn(prompt, history)

    executor = _get_executor()
    future = executor.submit(_runner)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        _reset_executor()
        print(
            f"[PrePrompt] Optimization timed out after {timeout}s — passing through",
            file=sys.stderr,
        )
        return {
            "optimized_prompt": prompt,
            "reason": "Optimization timed out — original prompt used.",
            "changes_made": [],
            "timed_out": True,
        }
    except Exception as e:
        print(f"[PrePrompt] Optimization failed: {e} — passing through", file=sys.stderr)
        return {
            "optimized_prompt": prompt,
            "reason": "Optimization unavailable — original prompt used.",
            "changes_made": [],
            "error": True,
        }


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
    keep_line   = _box_line("Keep enrichment: preprompt-rate keep")
    revert_line = _box_line("Use original:    preprompt-rate revert")
    footer = "╚" + "═" * (_WIDTH - 2) + "╝"
    return "\n".join([header] + reason_lines + [sep, orig_line] + opt_lines + [sep, keep_line, revert_line, footer])


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


def _chmod_user_only(path: Path) -> None:
    """Best-effort chmod 0o600 — silently no-op on Windows where it has no meaning."""
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


def _emit_event(event: str, properties: dict) -> None:
    """Fire a PostHog event from the hook. No-op if POSTHOG_API_KEY is unset."""
    try:
        from backend.analytics import track_event
        track_event(event, properties)
    except Exception:
        pass


def _maybe_emit_onboarding(was_intercepted: bool) -> None:
    """Print a one-time welcome message the first time PrePrompt processes a prompt."""
    flag = Path.home() / ".preprompt" / ".onboarded"
    if flag.exists():
        return
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
        _chmod_user_only(flag)
    except Exception:
        return
    if was_intercepted:
        print(
            "\n[PrePrompt] First optimization complete.\n"
            "  Original preserved at: ~/.preprompt/last_original.txt\n"
            "  Run preprompt-revert to restore original anytime.\n"
            "  Run preprompt-stats to see your optimization history.\n"
            "  Run preprompt-history to see all processed prompts.\n",
            file=sys.stderr,
        )
    else:
        print(
            "\n[PrePrompt] Running. Your prompt scored below threshold — passed through.\n"
            "  Run preprompt-test-classifier to see how prompts are scored.\n"
            "  Run preprompt-stats to track your usage over time.\n",
            file=sys.stderr,
        )


def _save_last_original(prompt: str) -> None:
    """Write the pre-optimization prompt to a fixed path so preprompt-revert can read it."""
    orig_file = Path.home() / ".preprompt" / "last_original.txt"
    try:
        orig_file.parent.mkdir(parents=True, exist_ok=True)
        orig_file.write_text(prompt)
        _chmod_user_only(orig_file)
    except Exception:
        pass


def _log_activity(score: int, was_intercepted: bool, original: str, optimized: str, reason: str) -> None:
    import datetime
    log_path = Path.home() / ".preprompt" / "activity.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not log_path.exists()
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
    if is_new:
        # Audit L-13: activity.log contains user prompts. On shared dev boxes
        # we don't want other UNIX users reading it. New file → restrict.
        _chmod_user_only(log_path)


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
    _chmod_user_only(sidecar_path)


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
        # Dev mode: add project root to sys.path so mcp_server is importable
        # without pip install. Safe no-op when running as a pip-installed package.
        _project_root = str(Path(__file__).resolve().parent.parent)
        if _project_root not in sys.path:
            sys.path.insert(0, _project_root)

        # Load .env: pip user path first, then dev-mode project root as fallback
        from dotenv import load_dotenv
        load_dotenv(Path.home() / ".preprompt" / ".env")
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")

        from mcp_server.classifier import route_prompt, get_clarifying_question
        routing = route_prompt(prompt, history, turn)
        route = routing["route"]
        score = routing["quality_score"]

        if route == "pass":
            _maybe_emit_onboarding(was_intercepted=False)
            _emit_event("prompt_processed", {
                "route": "pass", "score": score, "was_intercepted": False,
                "prompt_length": len(prompt),
            })
            passthrough()
            sys.exit(0)

        if route == "clarify":
            clarifying_q = get_clarifying_question(routing.get("missing_context", []))

            print(_render_clarify_annotation(score, clarifying_q, prompt), file=sys.stderr)

            clarified_prompt = (
                f"Before answering, ask the user this clarifying question and wait "
                f"for their response before proceeding:\n\n"
                f"\"{clarifying_q}\"\n\n"
                f"Original request: {prompt}"
            )

            try:
                _write_sidecar(prompt, prompt, score, False, turn, route="clarify")
            except Exception as sidecar_err:
                print(f"[PrePrompt] Sidecar write failed (clarify): {sidecar_err}",
                      file=sys.stderr)

            try:
                _log_activity(score, False, prompt, prompt, f"[CLARIFY] {clarifying_q}")
            except Exception as log_err:
                print(f"[PrePrompt] Activity log write failed: {log_err}", file=sys.stderr)

            _emit_event("prompt_processed", {
                "route": "clarify", "score": score, "was_intercepted": False,
                "prompt_length": len(prompt),
            })
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

        from mcp_server.secret_scanner import scan_for_secrets
        detected_secrets = scan_for_secrets(prompt)
        if detected_secrets:
            print(
                f"[PrePrompt SECURITY] Detected possible secrets in prompt: "
                f"{', '.join(detected_secrets)}. "
                f"Prompt will not be sent to optimization model.",
                file=sys.stderr,
            )
            _emit_event("secret_detected", {"count": len(detected_secrets)})
            _emit_event("prompt_processed", {
                "route": "secret_blocked", "score": score, "was_intercepted": False,
                "prompt_length": len(prompt),
            })
            print(json.dumps({"prompt": prompt}))
            sys.exit(0)

        from mcp_server.optimizer import optimize

        result = _optimize_with_timeout(optimize, prompt, history, timeout=2.0)
        optimized: str = result["optimized_prompt"]
        reason: str = result["reason"]
        was_intercepted: bool = optimized != prompt

        try:
            _write_sidecar(prompt, optimized, score, was_intercepted, turn, route="enrich")
        except Exception as sidecar_err:
            print(f"[PrePrompt] Sidecar write failed: {sidecar_err}", file=sys.stderr)

        if was_intercepted:
            _save_last_original(prompt)
            print(_render_annotation(score, reason, prompt, optimized), file=sys.stderr)
        else:
            print(f"[PrePrompt +{score}] {reason}", file=sys.stderr)

        try:
            _log_activity(score, was_intercepted, prompt, optimized, reason)
        except Exception as log_err:
            print(f"[PrePrompt] Activity log write failed: {log_err}", file=sys.stderr)

        _maybe_emit_onboarding(was_intercepted)

        _emit_event("prompt_processed", {
            "route": "enrich", "score": score, "was_intercepted": was_intercepted,
            "prompt_length": len(prompt), "optimized_length": len(optimized),
        })

        print(json.dumps({"prompt": optimized}))

    except Exception as e:
        print(f"[PrePrompt ERROR] {e}", file=sys.stderr)
        passthrough()

    sys.exit(0)


if __name__ == "__main__":
    main()
