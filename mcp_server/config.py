"""Centralised settings loaded from environment / .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = ""
    mcp_transport: str = "stdio"
    preprompt_model: str = "claude-haiku-4-5-20251001"


settings = Settings()
