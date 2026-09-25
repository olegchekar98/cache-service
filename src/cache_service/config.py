"""Service configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from the environment, prefixed with ``CACHE_SERVICE_``.

    ``create_app`` builds one per application, so tests pass their own instead of
    patching a module-level object.
    """

    model_config = SettingsConfigDict(env_prefix="CACHE_SERVICE_", env_file=".env", extra="ignore")

    # Needs an async driver: sqlite+aiosqlite or postgresql+asyncpg.
    database_url: str = "sqlite+aiosqlite:///./cache_service.db"
    log_level: str = "INFO"

    # Containers regularly start before their database accepts connections.
    database_startup_timeout_seconds: float = Field(default=10.0, ge=0)

    # The transformer stands in for a remote service. An artificial delay makes the
    # effect of the cache measurable without depending on anything external.
    transformer_latency_seconds: float = Field(default=0.0, ge=0.0)
    # Upper bound on transformer calls in flight across the whole process.
    transformer_max_concurrency: int = Field(default=10, ge=1)
