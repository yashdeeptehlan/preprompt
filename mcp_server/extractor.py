"""
Pure-heuristic stack signal extractor — no API calls.

Scans a prompt and recent conversation history for technology signals
(language, framework, style, database) and persists them into stack_memory.
"""

from storage.db import upsert_stack_memory


# ── Signal dictionaries ────────────────────────────────────────────────────────

# Order matters: more specific patterns are listed first to avoid mis-classification.
# Each entry is (list_of_keywords, detected_value).

_LANGUAGE_SIGNALS = [
    (["typescript", ".ts ", "interface ", ": string", ": number", ": boolean"], "typescript"),
    (["javascript", "const ", "async function", "require(", "module.exports"], "javascript"),
    (["python", ".py", "def ", "import ", "pip install", "virtualenv", "__init__"], "python"),
]

_FRAMEWORK_SIGNALS = [
    (["fastapi", "@app.", "uvicorn", "pydantic", "basemodel"], "fastapi"),
    (["django", "models.model", "django.db", "manage.py", "django.conf"], "django"),
    (["nextjs", "next.js", "getserversideprops", "getstaticprops", "next/router"], "nextjs"),
    (["react", "usestate", "useeffect", "jsx", "tsx", "react.fc", "react.component"], "react"),
    (["express", "app.get(", "req, res", "app.use(", "router.get(", "app.listen("], "express"),
]

_DB_SIGNALS = [
    (["duckdb"], "duckdb"),
    (["postgresql", "postgres", "asyncpg", "psycopg"], "postgres"),
    (["sqlite", "sqlite3"], "sqlite"),
    (["mysql", "pymysql", "mysqlclient"], "mysql"),
    (["mongodb", "pymongo", "motor", "mongoose"], "mongodb"),
    (["redis", "aioredis", "redis-py"], "redis"),
]

# Style signals are OR'd — first match wins
_STYLE_SIGNALS = [
    (["type hints", ": str", ": int", ": list", ": dict", ": bool", "-> ", "typed"], "typed"),
    (["async/await", "async def", "await ", "asyncio"], "async"),
    (["docstring", "docstrings", "# comment", "document", "documented"], "documented"),
]


def _build_scan_text(prompt: str, history: list) -> str:
    """Combine prompt + recent history into a single lowercase string to scan."""
    parts = [prompt]
    for turn in history[-6:]:
        content = turn.get("content", "")
        if isinstance(content, str):
            parts.append(content)
    return " ".join(parts).lower()


def extract_stack_signals(prompt: str, history: list) -> dict[str, str]:
    """
    Scan prompt and recent history for technology stack signals.

    Returns a {key: value} dict. Only keys with confident keyword matches
    are included. Returns empty dict if nothing detected.
    """
    text = _build_scan_text(prompt, history)
    signals: dict[str, str] = {}

    # Language
    for keywords, lang in _LANGUAGE_SIGNALS:
        if any(kw in text for kw in keywords):
            signals["language"] = lang
            break

    # Framework — may also imply language
    for keywords, framework in _FRAMEWORK_SIGNALS:
        if any(kw in text for kw in keywords):
            signals["framework"] = framework
            # Infer language from framework if not already detected
            if "language" not in signals:
                if framework in ("fastapi", "django"):
                    signals["language"] = "python"
                elif framework in ("react", "nextjs", "express"):
                    # Only infer if no stronger language signal was found
                    if "typescript" in text or ".ts " in text:
                        signals["language"] = "typescript"
                    else:
                        signals["language"] = "javascript"
            break

    # Database
    for keywords, db_name in _DB_SIGNALS:
        if any(kw in text for kw in keywords):
            signals["database"] = db_name
            break

    # Style
    for keywords, style in _STYLE_SIGNALS:
        if any(kw in text for kw in keywords):
            signals["style"] = style
            break

    return signals


def update_memory_from_prompt(prompt: str, history: list, project_id: str = "global") -> None:
    """Extract stack signals and upsert each into stack_memory, scoped to ``project_id``.

    Default ``"global"`` preserves pre-NW2 behaviour for callers that haven't
    yet been updated to pass a project identifier.
    """
    signals = extract_stack_signals(prompt, history)
    strong_signals = {"language", "framework"}
    for key, value in signals.items():
        confidence = 0.85 if key in strong_signals else 0.80
        upsert_stack_memory(key, value, confidence=confidence, project_id=project_id)
