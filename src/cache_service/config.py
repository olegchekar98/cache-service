"""Service configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from the environment, prefixed with ``CACHE_SERVICE_``."""

    model_config = SettingsConfigDict(env_prefix="CACHE_SERVICE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./cache_service.db"
    log_level: str = "INFO"

    # The transformer stands in for a remote service. An artificial delay makes the
    # effect of the cache measurable without depending on anything external.
    transformer_latency_seconds: float = Field(default=0.0, ge=0.0)


settings = Settings()
