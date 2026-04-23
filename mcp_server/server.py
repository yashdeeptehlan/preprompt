"""MCP server entry point for PrePrompt."""

import sys
from mcp_server.tools import mcp
from mcp_server.config import settings

_REGISTERED_TOOLS = ["optimize_prompt", "get_prompt_history"]


def main() -> None:
    print("──────────────────────────────────────────", flush=True, file=sys.stderr)
    print("  PrePrompt MCP server starting…",            flush=True, file=sys.stderr)
    print(f"  Transport : {settings.mcp_transport}",    flush=True, file=sys.stderr)
    print(f"  Tools     : {', '.join(_REGISTERED_TOOLS)}", flush=True, file=sys.stderr)
    print("──────────────────────────────────────────", flush=True, file=sys.stderr)
    mcp.run(transport=settings.mcp_transport)


if __name__ == "__main__":
    main()
