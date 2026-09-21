"""Own settings for this branch — DATABASE_URL and the Razorpay keys only.
Other branches (Authentication, teams) each define their own config too;
these get reconciled into one at merge time, not shared now.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENVIRONMENT: str = "development"

    # Filled in by whoever provisions Postgres (Railway per the other branches).
    DATABASE_URL: str = ""

    # --- Razorpay — TEST MODE keys first ---
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    # Generated separately in the Razorpay dashboard under Settings -> Webhooks
    # when the webhook URL is registered. NOT the same as RAZORPAY_KEY_SECRET.
    RAZORPAY_WEBHOOK_SECRET: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
