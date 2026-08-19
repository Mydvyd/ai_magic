from __future__ import annotations

import json
import os
import re
from typing import Any

from pydantic import BaseModel, Field, model_validator

from ._logging import logger

_GEMINI_MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9.-]*$")


class Settings(BaseModel):
    """Validated runtime configuration for ``AsyncAIMagic``.

    Settings may be constructed directly or loaded with ``from_env()``. Secret
    values are retained in memory as ordinary strings and are never fetched from
    external secret stores by this model.
    """

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
    max_sessions: int = Field(default=1000, ge=1)
    timeout: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=0, ge=0)
    max_credential_wait: float = Field(default=30.0, ge=0)

    @model_validator(mode="after")
    def validate_keys(self) -> Settings:
        if not self.credentials and not (self.groq_api_keys if self.provider == "groq" else self.gemini_api_keys):
            raise ValueError("At least one API credential is required")
        for credential in self.credentials:
            provider = str(credential.get("provider", self.provider)).strip().lower()
            if not credential.get("key"):
                raise ValueError("Every credential requires a non-empty key")
            raw_models = credential.get("models") or ()
            models = [raw_models] if isinstance(raw_models, str) else list(raw_models)
            if credential.get("model") is not None:
                models.append(credential["model"])
            if provider == "gemini":
                invalid = [
                    model for model in models if not isinstance(model, str) or not _GEMINI_MODEL_ID.fullmatch(model)
                ]
                if invalid:
                    raise ValueError(
                        "Gemini model IDs must be lowercase and use provider REST IDs (for example, 'gemini-3.7-flash')"
                    )
        providers = {str(item.get("provider", self.provider)).strip().lower() for item in self.credentials}
        if not self.credentials and self.gemini_api_keys:
            providers = {"gemini"}
        if providers == {"gemini"}:
            global_models = (self.primary_model, self.fallback_model)
            if any(not _GEMINI_MODEL_ID.fullmatch(model) for model in global_models):
                raise ValueError("Gemini primary_model/fallback_model must be lowercase provider REST IDs")
        return self

    @classmethod
    def from_env(cls, **overrides: object) -> Settings:
        """Build settings from supported environment variables.

        Explicit keyword overrides take precedence over environment values.
        Invalid numeric environment values are logged and replaced with defaults;
        malformed ``AI_MAGIC_CREDENTIALS`` JSON is rejected.

        Args:
            **overrides: Field values applied after reading the environment.

        Returns:
            A validated settings instance.

        Raises:
            ValueError: If credential JSON is malformed or credential/model
                validation fails.
            pydantic.ValidationError: If an environment or override value cannot
                be validated as the target field type.
        """

        def keys(name: str) -> list[str]:
            return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]

        def env_float(name: str, default: float) -> float | str:
            raw = os.getenv(name)
            if raw is None:
                return default
            try:
                return float(raw)
            except ValueError:
                logger.warning("Ignoring invalid %s=%r; using default %s", name, raw, default)
                return default

        def env_int(name: str, default: int) -> int | str:
            raw = os.getenv(name)
            if raw is None:
                return default
            try:
                return int(raw)
            except ValueError:
                logger.warning("Ignoring invalid %s=%r; using default %s", name, raw, default)
                return default

        raw_credentials = os.getenv("AI_MAGIC_CREDENTIALS", "")
        parsed_credentials: list[Any] = []
        if raw_credentials:
            try:
                parsed_credentials = json.loads(raw_credentials)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"AI_MAGIC_CREDENTIALS must be valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"
                ) from exc
        data: dict[str, object] = {
            "provider": os.getenv("AI_MAGIC_PROVIDER", "groq"),
            "credentials": parsed_credentials,
            "groq_api_keys": keys("GROQ_API_KEYS") or keys("GROQ_API_KEY"),
            "gemini_api_keys": keys("GEMINI_API_KEYS") or keys("GEMINI_API_KEY"),
            "openrouter_referer": os.getenv("OPENROUTER_HTTP_REFERER"),
            "openrouter_title": os.getenv("OPENROUTER_X_TITLE"),
            "primary_model": os.getenv("AI_MAGIC_PRIMARY_MODEL", "llama-3.3-70b-versatile"),
            "fallback_model": os.getenv("AI_MAGIC_FALLBACK_MODEL", "llama-3.1-8b-instant"),
            "timeout": env_float("AI_MAGIC_TIMEOUT", 60.0),
            "max_retries": env_int("AI_MAGIC_MAX_RETRIES", 0),
            "max_credential_wait": env_float("AI_MAGIC_MAX_CREDENTIAL_WAIT", 30.0),
            "max_sessions": env_int("AI_MAGIC_MAX_SESSIONS", 1000),
        }
        data.update(overrides)
        return cls.model_validate(data)
