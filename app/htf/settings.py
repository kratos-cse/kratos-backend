"""Settings for HTF 2026 modules, isolated from KRATOS core."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class HTFSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    HTF_RAZORPAY_WEBHOOK_SECRET: str = ""
    GOOGLE_DRIVE_AUTH_MODE: str = "service_account"  # "service_account" or "oauth"
    GOOGLE_DRIVE_CREDENTIALS_JSON: str = ""
    GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN: str = ""
    GOOGLE_DRIVE_OAUTH_CLIENT_ID: str = ""
    GOOGLE_DRIVE_OAUTH_CLIENT_SECRET: str = ""
    GOOGLE_DRIVE_ROOT_FOLDER_ID: str = ""
    GOOGLE_DRIVE_TIMEOUT_SECONDS: int = 30
    HTF_PPT_MAX_BYTES: int = 50 * 1024 * 1024  # 50 MB


htf_settings = HTFSettings()
