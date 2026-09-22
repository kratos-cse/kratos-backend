import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.main import app


def test_jwt_secret_validation_in_dev_and_test():
    # Development environment allows default secret
    s_dev = Settings(ENVIRONMENT="development", JWT_SECRET_KEY="dev-secret-change-me")
    assert s_dev.JWT_SECRET_KEY == "dev-secret-change-me"

    s_test = Settings(ENVIRONMENT="test", JWT_SECRET_KEY="dev-secret-change-me")
    assert s_test.JWT_SECRET_KEY == "dev-secret-change-me"


def test_jwt_secret_validation_in_production():
    # Production environment rejects default secret
    with pytest.raises(ValidationError):
        Settings(ENVIRONMENT="production", JWT_SECRET_KEY="dev-secret-change-me")

    # Production environment rejects short secret
    with pytest.raises(ValidationError):
        Settings(ENVIRONMENT="production", JWT_SECRET_KEY="short-secret-under-32-chars")

    # Production environment accepts long random secret
    s_prod = Settings(
        ENVIRONMENT="production",
        JWT_SECRET_KEY="super_secret_high_entropy_key_longer_than_32_characters_123456",
    )
    assert s_prod.JWT_SECRET_KEY.startswith("super_secret_high_entropy")


def test_media_receipts_static_mount_removed():
    # Ensure static unauthenticated mount is not present in routes
    mounted_paths = [getattr(route, "path", "") for route in app.routes]
    assert "/media/receipts" not in mounted_paths
