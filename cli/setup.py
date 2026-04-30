"""
First-run setup wizard for PrePrompt.
Runs automatically if no API key is configured.
"""
import os
from pathlib import Path


def _get_env_path() -> Path:
    return Path.home() / ".preprompt" / ".env"


def _key_is_configured() -> bool:
    """Check if API key exists in env, ~/.preprompt/.env, or a local .env file."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    candidates = [
        _get_env_path(),
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for env_path in candidates:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    val = line.split("=", 1)[1].strip()
                    if val and val != "your-key-here":
                        return True
    return False


def maybe_run_setup() -> None:
    """
    If no API key configured, show setup wizard.
    Called at the top of stats_cmd() and history_cmd().
    """
    if _key_is_configured():
        return

    print()
    print("  ┌─────────────────────────────────────────────────────┐")
    print("  │  Welcome to PrePrompt — first-time setup            │")
    print("  └─────────────────────────────────────────────────────┘")
    print()
    print("  PrePrompt needs an Anthropic API key to optimize prompts.")
    print("  This is SEPARATE from a Claude.ai subscription.")
    print()
    print("  To get your free API key:")
    print("  1. Go to: https://console.anthropic.com/api-keys")
    print("  2. Sign up (free)")
    print("  3. Click 'Create Key'")
    print("  4. New accounts get $5 free credit — no card needed")
    print()
    print("  Cost: ~$0.001 per optimized prompt (~$1-3/month typical)")
    print()

    key = input("  Paste your API key here (or press Enter to skip): ").strip()

    if not key:
        print()
        print("  Skipped. Add your key later:")
        env_path = _get_env_path()
        print(f"  echo 'ANTHROPIC_API_KEY=sk-ant-...' >> {env_path}")
        print()
        return

    env_path = _get_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)

    existing = env_path.read_text() if env_path.exists() else ""
    if "ANTHROPIC_API_KEY" not in existing:
        with open(env_path, "a") as f:
            f.write(f"\nANTHROPIC_API_KEY={key}\n")
    else:
        lines = existing.splitlines()
        lines = [
            f"ANTHROPIC_API_KEY={key}" if ln.startswith("ANTHROPIC_API_KEY=")
            else ln for ln in lines
        ]
        env_path.write_text("\n".join(lines) + "\n")

    print()
    print("  ✓ API key saved to ~/.preprompt/.env")
    print()
    print("  PrePrompt is ready. Run preprompt-install to register")
    print("  hooks in Claude Code and Cursor.")
    print()
