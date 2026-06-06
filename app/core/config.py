from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "CodeScribe"
    app_env: str = "local"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    codescribe_mode: str = "webhook_server"

    database_url: str = "sqlite+aiosqlite:///./codescribe.db"
    redis_url: str = "redis://localhost:6379/0"

    github_webhook_secret: str = Field(default="dev-secret")
    github_api_base_url: str = "https://api.github.com"
    github_app_id: str | None = None
    github_app_private_key: str | None = None
    github_token: str | None = None

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-1.5-pro"

    llm_provider: str = "ollama"
    llm_request_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    local_model: str = "Qwen/Qwen2.5-0.5B-Instruct"
    local_model_timeout_seconds: float = 45.0
    generic_llm_api_base_url: str | None = None
    generic_llm_api_key: str | None = None
    generic_llm_model: str = "gpt-4o-mini"

    auto_post_reviews: bool = False
    post_pr_comment: bool = False
    training_dataset_path: str = "outputs/training_dataset.jsonl"

    publish_mode: str = "dry_run"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def is_local(self) -> bool:
        return self.app_env.lower() in {"local", "dev", "development", "test"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
