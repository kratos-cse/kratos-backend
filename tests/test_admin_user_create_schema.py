import uuid

import pytest
from pydantic import ValidationError

from app.schemas.admin import AdminUserCreate


def test_admin_user_create_accepts_email():
    role_id = uuid.uuid4()
    body = AdminUserCreate(email="ops@example.com", role_id=role_id)
    assert body.email == "ops@example.com"
    assert body.user_id is None


def test_admin_user_create_accepts_user_id():
    role_id = uuid.uuid4()
    user_id = uuid.uuid4()
    body = AdminUserCreate(user_id=user_id, role_id=role_id)
    assert body.user_id == user_id
    assert body.email is None


def test_admin_user_create_rejects_both_identifiers():
    with pytest.raises(ValidationError):
        AdminUserCreate(
            user_id=uuid.uuid4(),
            email="ops@example.com",
            role_id=uuid.uuid4(),
        )


def test_admin_user_create_requires_identifier():
    with pytest.raises(ValidationError):
        AdminUserCreate(role_id=uuid.uuid4())


def test_admin_user_create_accepts_event_ids():
    role_id = uuid.uuid4()
    event_a = uuid.uuid4()
    event_b = uuid.uuid4()
    body = AdminUserCreate(email="coord@example.com", role_id=role_id, event_ids=[event_a, event_b])
    assert body.event_ids == [event_a, event_b]
