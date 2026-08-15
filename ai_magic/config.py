from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Settings(BaseModel):
    provider: str = "groq"
    credentials: list[dict[str, Any]] = Field(default_factory=list)
    groq_api_keys: list[str] = Field(default_factory=list)
    gemini_api_keys: list[str] = Field(default_factory=list)
    groq_base_url: str = "https://api.groq.com/openai/v1"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    openrouter_referer: str | None = None
    openrouter_title: str | None = None
    primary_model: str = "llama-3.3-70b-versatile"
    fallback_model: str = "llama-3.1-8b-instant"
    default_history_limit: int = Field(default=10, ge=2)
    timeout: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=0, ge=0)
    max_credential_wait: float = Field(default=30.0, ge=0)

    @model_validator(mode="after")
    def validate_keys(self) -> "Settings":
        if not self.credentials and not (self.groq_api_keys if self.provider == "groq" else self.gemini_api_keys):
            raise ValueError("At least one API credential is required")
        return self

    @classmethod
    def from_env(cls, **overrides: object) -> "Settings":
        def keys(name: str) -> list[str]:
            return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]

        raw_credentials = os.getenv("AI_MAGIC_CREDENTIALS", "")
        data: dict[str, object] = {
            "provider": os.getenv("AI_MAGIC_PROVIDER", "groq"),
            "credentials": json.loads(raw_credentials) if raw_credentials else [],
            "groq_api_keys": keys("GROQ_API_KEYS") or keys("GROQ_API_KEY"),
            "gemini_api_keys": keys("GEMINI_API_KEYS") or keys("GEMINI_API_KEY"),
            "openrouter_referer": os.getenv("OPENROUTER_HTTP_REFERER"),
            "openrouter_title": os.getenv("OPENROUTER_X_TITLE"),
        }
        data.update(overrides)
        return cls.model_validate(data)
