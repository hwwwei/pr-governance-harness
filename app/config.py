from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./harness.db"
    redis_url: str = "redis://localhost:6379/0"
    model_provider: str = "mock"
    model_base_url: str | None = None
    model_name: str = "default-model"
    model_api_key: str | None = None
    github_token: str | None = None
    github_webhook_secret: str | None = None
    auto_activate_evolution: bool = True
    run_in_process: bool = True
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
