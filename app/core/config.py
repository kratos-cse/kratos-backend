"""
Central application configuration.

DATABASE_URL and GOOGLE_CLIENT_ID are read from the environment and are
expected to be BLANK until the team lead provisions the Railway Postgres
instance and the Google OAuth client (see .env.example). The app is still
importable with them blank so `uvicorn app.main:app` and the Swagger UI at
/docs come up fine — any request that actually needs the DB or Google
verification will fail with a clear error until those are set.
"""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENVIRONMENT: str = "development"

    # --- Database (filled in by team lead once Railway Postgres exists) ---
    DATABASE_URL: str = ""

    # --- Google OAuth (filled in by team lead) ---
    GOOGLE_CLIENT_ID: str = ""

    # --- JWT session signing ---
    JWT_SECRET_KEY: str = "dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # --- CORS ---
    CORS_ORIGINS: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def async_database_url(self) -> str:
        """DATABASE_URL normalized to the asyncpg driver, for the app's runtime engine."""
        url = self.DATABASE_URL
        if not url:
            return url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
