from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LANGKEY_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://langkey:langkey@localhost:5433/langkey"
    jwt_secret: str = "dev-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60 * 24 * 7

    # S3-compatible object storage (MinIO in dev); used from M2 for audio assets.
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "langkey"
    s3_secret_key: str = "langkey-dev"
    s3_bucket: str = "langkey-content"

    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
