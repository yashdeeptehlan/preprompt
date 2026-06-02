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

# ── Route-layer constants ──────────────────────────────────────────────────────

_VAGUE_ACTION_VERBS = frozenset({
    "make", "fix", "improve", "update", "change", "do", "help", "clean", "sort",
})

_MULTI_WORD_VAGUE_PHRASES = frozenset({
    "make it", "make this", "make that",
    "make it better", "make this better", "make that better",
    "make it work", "make this work",
    "fix it", "fix this", "fix that",
    "improve it", "improve this", "improve that",
    "clean up", "sort out", "help me", "do this", "do that",
})

_QUESTION_STARTERS = frozenset({
    "what", "how", "why", "when", "where", "is", "does", "can", "should",
})

_GENERIC_OBJECTS = frozenset({
    "it", "this", "that", "them", "things", "everything", "stuff",
    "bug", "issue", "error", "problem", "code", "file", "thing",
})

_STOP_WORDS = frozenset({
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "with",
    "that", "this", "be", "by", "or", "and", "but", "not", "from",
    "as", "into", "about",
})

_CLEAR_ACTION_VERBS = frozenset({
    "implement", "refactor", "migrate", "deploy", "configure", "integrate",
    "optimize", "debug", "test", "write", "create", "build", "add", "remove",
    "delete", "update", "fix",
})


def _has_technical_noun(words: list) -> bool:
    """True if any word is longer than 4 chars, not a generic object, not a stop word."""
    return any(
        len(w) > 4 and w not in _GENERIC_OBJECTS and w not in _STOP_WORDS
        for w in words
    )


def _check_clarify(lower: str, words: list, score: int) -> tuple:
    """Return (is_clarify: bool, missing_context: list[str])."""
    word_count = len(words)

    # Exact match with known vague multi-word phrases
    if lower.strip() in _MULTI_WORD_VAGUE_PHRASES:
        return True, ["target area", "desired outcome"]

    # Rule 1: under 4 words AND score >= threshold AND no specific technical noun
    if word_count < 4 and score >= OPTIMIZATION_THRESHOLD and not _has_technical_noun(words):
        return True, ["target area", "desired outcome"]

    # Rule 2: short prompt (≤5 words) with vague verb + generic/no object
    if word_count <= 5 and words and words[0] in _VAGUE_ACTION_VERBS:
        meaningful = [w for w in words[1:] if w not in {"the", "a", "an", "my", "our", "your"}]
        if not meaningful:
            return True, ["target area", "desired outcome"]
        if len(meaningful) <= 2 and all(w in _GENERIC_OBJECTS for w in meaningful):
            has_bug_word = any(w in {"bug", "issue", "error", "problem"} for w in meaningful)
            missing = ["target file or component", "desired outcome"] if has_bug_word \
                else ["target area", "desired outcome"]
            return True, missing

    return False, []


def classify_prompt(prompt: str, history: list, turn: int) -> int:
    """Score *prompt* from 0–100; higher = more benefit from optimization.

    Scoring breakdown
    -----------------
    Positive (up to 25 pts each):
      • Ambiguity markers (vague verbs)
      • Multi-requirement density ("and", comma-separated tasks)
      • Specific technical noun present (+25)
      • Clear action verb + specific object, 2-6 words (+15)
      • Turn depth (turns 3+ add points, up to 15)
      • Code task without format hint (+15)

    Negative signals:
      • < 4 words with no code task and no technical noun: -20
      • Starts with "what is/does/are": -15
      • Already structured (numbered steps or explicit format): -15
      • Conversational opener: -25
    """
    score = 0
    lower = prompt.lower().strip()
    words = lower.split()
    word_count = len(words)

    # Compute these first — needed by the conditional short-prompt penalty
    has_numbered_steps = bool(re.search(r"(?:^|\s)\d+\.\s", prompt))
    has_explicit_format = any(kw in lower for kw in _FORMAT_KEYWORDS)
    has_code_task = any(kw in lower for kw in _CODE_KEYWORDS)
    has_tech_noun = _has_technical_noun(words)

    # ── Negative signals ──────────────────────────────────────────────────────

    # Conversational opener
    if words and words[0] in _CONVERSATIONAL:
        score -= 25

    # Short penalty: only when truly bare (< 4 words, no code keyword, no tech noun)
    if word_count < 4 and not has_code_task and not has_tech_noun:
        score -= 20

    # Pure lookup / definition question
    if re.match(r"^what (is|does|are)\b", lower):
        score -= 15

    # Already structured: numbered list OR explicit format keyword present
    if has_numbered_steps or has_explicit_format:
        score -= 15

    # ── Positive signals ──────────────────────────────────────────────────────

    # 1. Ambiguity markers (up to 25 pts) — substring match catches "handles", "managing", etc.
    ambiguity_hits = sum(1 for v in _AMBIGUITY_VERBS if v in lower)
    score += min(ambiguity_hits * 25, 25)

    # 2. Multi-requirement density (up to 30 pts)
    and_count = lower.count(" and ")
    comma_count = lower.count(",")
    multi_hits = and_count + comma_count
    score += min(multi_hits * 12, 30)

    # 3. Turn depth (up to 15 pts): turns 1–2 contribute nothing; 3+ add proportionally
    if turn >= 3:
        score += min((turn - 2) * 5, 15)

    # 4. Code task without format hint (+15)
    if has_code_task and not has_explicit_format:
        score += 15

    # 5. Specific technical noun present (+25)
    if has_tech_noun:
        score += 25

    # 6. Clear action verb with specific technical object, short prompt (+15)
    if 2 <= word_count <= 6 and words and words[0] in _CLEAR_ACTION_VERBS and has_tech_noun:
        score += 15

    return score


def route_prompt(prompt: str, history: list, turn: int) -> dict:
    """Return routing decision for a prompt.

    Does NOT make API calls — pure heuristics only.

    Returns dict with keys:
      route            : "pass" | "enrich" | "clarify"
      quality_score    : int (same as classify_prompt output)
      intent_confidence: int (0-100, heuristic estimate)
      risk_level       : "low" | "medium" | "high"
      reason           : str (one sentence)
      missing_context  : list[str] (empty if route != "clarify")
    """
    score = classify_prompt(prompt, history, turn)
    lower = prompt.lower().strip()
    words = lower.split()
    word_count = len(words)

    # ── CLARIFY check (evaluated before threshold) ────────────────────────
    is_clarify, missing_context = _check_clarify(lower, words, score)
    if is_clarify:
        return {
            "route": "clarify",
            "quality_score": score,
            "intent_confidence": 25 if word_count < 4 else 30,
            "risk_level": "high" if word_count <= 3 else "medium",
            "reason": "Prompt is too vague to optimize safely without risking scope expansion.",
            "missing_context": missing_context,
        }

    # ── PASS check ────────────────────────────────────────────────────────
    is_question = bool(words and words[0] in _QUESTION_STARTERS)
    if score < OPTIMIZATION_THRESHOLD or (is_question and score < 50):
        return {
            "route": "pass",
            "quality_score": score,
            "intent_confidence": 90 if is_question else 80,
            "risk_level": "low",
            "reason": (
                f"Score {score} is below threshold {OPTIMIZATION_THRESHOLD}; "
                "prompt is clear enough."
            ),
            "missing_context": [],
        }

    # ── ENRICH ────────────────────────────────────────────────────────────
    if word_count > 12:
        conf = 75
    elif word_count > 7:
        conf = 65
    else:
        conf = 60

    return {
        "route": "enrich",
        "quality_score": score,
        "intent_confidence": conf,
        "risk_level": "medium" if score < 60 else "low",
        "reason": "Prompt can be enriched with more technical specificity and context.",
        "missing_context": [],
    }


_CLARIFY_TEMPLATES: dict[str, str] = {
    "target area": (
        "What specifically should be improved: UI/UX, performance, "
        "code quality, accessibility, or architecture?"
    ),
    "desired outcome": "What should the end result look like?",
    "scope boundary": "Should this be a minimal targeted fix or a broader refactor?",
    "target file or component": (
        "Which file, component, or function should this apply to?"
    ),
}


def get_clarifying_question(missing_context: list) -> str:
    for ctx in missing_context:
        if ctx in _CLARIFY_TEMPLATES:
            return _CLARIFY_TEMPLATES[ctx]
    return "What specifically do you want changed, and what should the result look like?"
