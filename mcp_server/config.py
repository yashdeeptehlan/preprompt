"""Centralised settings loaded from environment / .env file.

Audit L-9: the API key used to be embedded in ``~/.claude/settings.json`` as
plaintext under the MCP server's ``env`` block. We now also look in
``~/.preprompt/.env`` at startup so the key can live in a file we own (chmod
600) and isn't serialised into the IDE config or its backups.
"""

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load order: project-local .env (dev), then ~/.preprompt/.env. Existing env
# vars win — explicit os.environ from the parent process is never overridden.
load_dotenv(Path(".env"), override=False)
load_dotenv(Path.home() / ".preprompt" / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = ""
    mcp_transport: str = "stdio"
    preprompt_model: str = "claude-haiku-4-5-20251001"


settings = Settings()
