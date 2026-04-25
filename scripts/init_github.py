#!/usr/bin/env python3
"""
Initialize git and prepare for GitHub push.

Usage:
    python scripts/init_github.py
"""

import subprocess
import sys
from pathlib import Path


def _run(args: list[str], cwd: Path, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=str(cwd),
        capture_output=capture,
        text=True,
    )


def main() -> None:
    repo_root = Path(__file__).parent.parent.resolve()
    git_dir   = repo_root / ".git"

    # ── git init ──────────────────────────────────────────────────────────────
    if not git_dir.exists():
        result = _run(["git", "init"], repo_root)
        if result.returncode != 0:
            print(f"git init failed: {result.stderr.strip()}", file=sys.stderr)
            sys.exit(1)

    # ── git add . ─────────────────────────────────────────────────────────────
    result = _run(["git", "add", "."], repo_root)
    if result.returncode != 0:
        print(f"git add failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    # ── git commit ────────────────────────────────────────────────────────────
    result = _run(
        ["git", "-c", "user.name=yashdeeptehlan", "-c", "user.email=0yashdeeptehlan@gmail.com",
         "commit", "-m", "feat: PrePrompt v0.1 — MCP prompt optimizer"],
        repo_root,
    )
    if result.returncode != 0:
        # Nothing to commit (already committed) — not a fatal error
        stderr = result.stderr.strip()
        if "nothing to commit" not in stderr and "nothing added" not in stderr:
            print(f"git commit failed: {stderr}", file=sys.stderr)
            sys.exit(1)

    print("✓ Git initialized and first commit made")
    print()
    print("Next steps to push to GitHub:")
    print("  1. Create a new repo at https://github.com/new")
    print("     Name: preprompt")
    print("     Description: MCP server that intercepts and optimizes prompts in Claude Code + Cursor")
    print("     Visibility: Public (recommended — open source moat)")
    print("  2. Run:")
    print("     git remote add origin https://github.com/YOUR_USERNAME/preprompt.git")
    print("     git branch -M main")
    print("     git push -u origin main")
    print()
    print("⚠ Make sure .env is in .gitignore before pushing (it is — already added in Phase 2)")


if __name__ == "__main__":
    main()
