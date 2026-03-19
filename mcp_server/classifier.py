"""Pure-heuristic prompt classifier — no API calls, returns a score 0–100."""

import re

# Vague/ambiguous action verbs that signal an under-specified prompt
_AMBIGUITY_VERBS = [
    "handle", "manage", "fix", "make it work", "deal with",
    "update", "improve", "refactor", "clean up", "sort out",
]

# Keywords that suggest a code/structured-output task
_CODE_KEYWORDS = [
    "function", "class", "api", "endpoint", "component", "middleware",
    "script", "write", "create", "implement", "build", "add", "set up",
]

# Keywords that indicate the user already specified an output format
_FORMAT_KEYWORDS = [
    "json", "list", "array", "string", "dict", "object",
    "html", "markdown", "table", "as a ", "return type", "output format",
]

# Single-word conversational openers that signal a non-substantive turn
_CONVERSATIONAL = {
    "yes", "no", "ok", "okay", "thanks", "thank", "cool",
    "great", "sure", "alright", "yep", "nope", "gotcha",
}

OPTIMIZATION_THRESHOLD = 38


def classify_prompt(prompt: str, history: list, turn: int) -> int:
    """Score *prompt* from 0–100; higher = more benefit from optimization.

    Scoring breakdown
    -----------------
    High weight (up to 25 pts each):
      • Ambiguity markers (vague verbs)
      • Multi-requirement density ("and", comma-separated tasks)

    Medium weight (up to 15 pts each):
      • Turn depth (turns 3+ add points)
      • Output-format ambiguity (code task with no format hint)

    Negative signals:
      • Prompt < 6 words            : -20
      • Starts with "what is/does/are": -15
      • Already has numbered steps
        or explicit output format   : -15
      • Conversational opener       : -25
    """
    score = 0
    lower = prompt.lower().strip()
    words = lower.split()
    word_count = len(words)

    # ── Negative signals ──────────────────────────────────────────────────────

    # Conversational opener
    if words and words[0] in _CONVERSATIONAL:
        score -= 25

    # Very short prompt
    if word_count < 6:
        score -= 20

    # Pure lookup / definition question
    if re.match(r"^what (is|does|are)\b", lower):
        score -= 15

    # Already structured: numbered list OR explicit format keyword present
    has_numbered_steps = bool(re.search(r"(?:^|\s)\d+\.\s", prompt))
    has_explicit_format = any(kw in lower for kw in _FORMAT_KEYWORDS)
    if has_numbered_steps or has_explicit_format:
        score -= 15

    # ── Positive signals ──────────────────────────────────────────────────────

    # 1. Ambiguity markers (up to 25 pts) — substring match catches "handles", "managing", etc.
    ambiguity_hits = sum(1 for v in _AMBIGUITY_VERBS if v in lower)
    score += min(ambiguity_hits * 25, 25)

    # 2. Multi-requirement density (up to 25 pts)
    and_count = lower.count(" and ")
    comma_count = lower.count(",")
    multi_hits = and_count + comma_count
    score += min(multi_hits * 12, 30)

    # 3. Turn depth (up to 15 pts): turns 1–2 contribute nothing; 3+ add proportionally
    if turn >= 3:
        score += min((turn - 2) * 5, 15)

    # 4. Output-format ambiguity (up to 15 pts): code task with no format hint
    has_code_task = any(kw in lower for kw in _CODE_KEYWORDS)
    if has_code_task and not has_explicit_format:
        score += 15

    return score
