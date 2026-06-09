"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_bot_token: str = Field(..., description="Telegram bot token from @BotFather")

    # Either one ``SERPER_API_KEY`` or several CSV-separated keys in
    # ``SERPER_API_KEYS``. The bot rotates through keys when one is exhausted
    # (HTTP 401/402/403/429) so that running out of credits on one key
    # transparently falls over to the next.
    serper_api_key: str = Field(
        default="",
        description="API key for serper.dev (single-key mode).",
    )
    serper_api_keys_raw: str = Field(
        default="",
        alias="serper_api_keys",
        description="CSV / newline-separated list of serper.dev API keys for rotation.",
    )

    # Bootstrap admins. Either ID or @username (without the @) — the first
    # /start from a matching user promotes them to ``is_admin=1`` in the
    # users table. After that, the DB is the source of truth.
    admin_user_ids_raw: str = Field(
        default="",
        alias="admin_user_ids",
        description="CSV of Telegram user IDs that should be promoted to admin on first /start.",
    )
    admin_usernames_raw: str = Field(
        default="",
        alias="admin_usernames",
        description=(
            "CSV of Telegram usernames (no @) that should be promoted to admin on first /start."
        ),
    )

    # Pre-approved user IDs. Listed users skip the admin-approval flow.
    # Empty (default) means everyone needs admin approval to use the bot.
    allowed_user_ids_raw: str = Field(
        default="",
        alias="allowed_user_ids",
        description="CSV of Telegram user IDs auto-approved on first /start (bypass admin gate).",
    )

    # Default per-search caps. Result count is also user-overridable from
    # the bot's settings UI (10 / 25 / 50 / 100).
    max_results: int = Field(default=50, ge=1, le=200)
    result_count_choices_raw: str = Field(
        default="10,25,50,100",
        alias="result_count_choices",
        description="CSV of allowed values shown in the result-count picker.",
    )
    search_timeout_seconds: float = Field(default=20.0, gt=0)
    log_level: str = Field(default="INFO")

    # Tunables for the search pipeline.
    # NOTE: serper.dev free tier rejects ``num`` values > 20 with a 400
    # "Query not allowed". Paid tiers go higher; tune as needed.
    results_per_query: int = Field(default=10, ge=1, le=100)
    max_search_calls: int = Field(default=15, ge=1, le=100)
    verification_concurrency: int = Field(default=8, ge=1, le=64)
    oembed_timeout_seconds: float = Field(default=10.0, gt=0)

    # Persistence for search history. SQLite path; created on first run.
    history_db_path: Path = Field(
        default=Path("history.db"),
        description="Filesystem path to the SQLite history database.",
    )
    history_per_page: int = Field(default=10, ge=1, le=50)
    results_per_page: int = Field(default=10, ge=1, le=50)

    @cached_property
    def allowed_user_ids(self) -> list[int]:
        return [int(x.strip()) for x in self.allowed_user_ids_raw.split(",") if x.strip()]

    @cached_property
    def admin_user_ids(self) -> list[int]:
        return [int(x.strip()) for x in self.admin_user_ids_raw.split(",") if x.strip()]

    @cached_property
    def admin_usernames(self) -> list[str]:
        return [
            x.strip().lstrip("@").lower() for x in self.admin_usernames_raw.split(",") if x.strip()
        ]

    @cached_property
    def result_count_choices(self) -> list[int]:
        choices: list[int] = []
        for raw in self.result_count_choices_raw.split(","):
            chunk = raw.strip()
            if not chunk:
                continue
            try:
                value = int(chunk)
            except ValueError:
                continue
            if 1 <= value <= 200 and value not in choices:
                choices.append(value)
        return choices or [10, 25, 50, 100]

    @cached_property
    def serper_api_keys(self) -> list[str]:
        """Return the de-duplicated list of Serper API keys, in priority order."""
        keys: list[str] = []
        seen: set[str] = set()

        # Multi-key field first so its order takes precedence.
        for chunk in self.serper_api_keys_raw.replace("\n", ",").split(","):
            key = chunk.strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)

        # Fall back to / merge in the legacy single-key field.
        if self.serper_api_key:
            key = self.serper_api_key.strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)

        return keys

    def model_post_init(self, __context: object) -> None:
        if not self.serper_api_keys:
            raise ValueError(
                "No Serper API keys configured: set SERPER_API_KEY "
                "or SERPER_API_KEYS (comma-separated)."
            )


def load_settings() -> Settings:
    """Load settings from environment / .env."""
    return Settings()  # type: ignore[call-arg]
