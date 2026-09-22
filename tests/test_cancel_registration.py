import uuid
import pytest
from app.main import app
from app.models.enums import RegistrationStatus, PaymentStatus, TeamMemberStatus, TeamStatus


def test_cancel_registration_route_registered():
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/v1/registrations/{registration_id}/cancel" in paths
