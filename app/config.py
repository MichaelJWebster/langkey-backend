from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


DEV_JWT_SECRET = "dev-secret-change-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LANGKEY_", env_file=".env", extra="ignore")

    environment: str = "dev"  # "dev" | "prod"
    # Day boundaries for streaks / "reviewed today" (the learner's wall clock).
    timezone: str = "Australia/Sydney"
    database_url: str = "postgresql+asyncpg://langkey:langkey@localhost:5433/langkey"
    jwt_secret: str = DEV_JWT_SECRET

    # Initial login seeded outside dev (in dev a fixed dev user is seeded).
    admin_email: str | None = None
    admin_password: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60 * 24 * 7

    # S3-compatible object storage (MinIO in dev); used from M2 for audio assets.
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "langkey"
    s3_secret_key: str = "langkey-dev"
    s3_bucket: str = "langkey-content"
    s3_region: str = "auto"

    cors_origins: list[str] = ["http://localhost:5173"]


def check_production_config(settings: Settings) -> None:
    """Refuse to start outside dev with dev-grade security settings."""
    if settings.environment == "dev":
        return
    if settings.jwt_secret == DEV_JWT_SECRET:
        raise RuntimeError(
            "LANGKEY_JWT_SECRET must be set to a real secret when "
            "LANGKEY_ENVIRONMENT is not 'dev'"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
