"""Unit tests for roster duplicate existence helpers (no DATABASE_URL required)."""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import team_service


def _result_with_first(row):
    result = MagicMock()
    result.first.return_value = row
    return result


def _result_scalar_one_or_none_raises_multiple():
    result = MagicMock()
    from sqlalchemy.exc import MultipleResultsFound

    def _raise():
        raise MultipleResultsFound()

    result.scalar_one_or_none.side_effect = _raise
    result.first.return_value = (uuid.uuid4(),)
    return result


@pytest.mark.asyncio
async def test_profile_phone_exists_uses_first_not_scalar_one():
    """Regression: many matching rows must not raise MultipleResultsFound."""
    db = AsyncMock()
    db.execute.return_value = _result_scalar_one_or_none_raises_multiple()

    found = await team_service._exists_non_terminal_member_profile_phone_on_event(
        db, uuid.uuid4(), "98111111111"
    )
    assert found is True
    db.execute.assert_awaited_once()
    stmt = db.execute.await_args.args[0]
    assert getattr(stmt, "_limit_clause", None) is not None or "LIMIT" in str(stmt).upper()


@pytest.mark.asyncio
async def test_profile_phone_absent_returns_false():
    db = AsyncMock()
    db.execute.return_value = _result_with_first(None)

    found = await team_service._exists_non_terminal_member_profile_phone_on_event(
        db, uuid.uuid4(), "90000000000"
    )
    assert found is False


@pytest.mark.asyncio
async def test_member_criteria_exists_returns_true_with_one_row():
    db = AsyncMock()
    db.execute.return_value = _result_with_first((uuid.uuid4(),))

    from app.models.team import TeamMember

    found = await team_service._exists_non_terminal_member_on_event(
        db, uuid.uuid4(), TeamMember.phone == "91111111111"
    )
    assert found is True
