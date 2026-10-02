"""Contracts with other people/components (Person 2 and Person 4).

These functions encapsulate cross-domain dependencies so that HTF Person 3
logic can operate without hardcoding table assumptions of other components.
"""
import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TeamMemberStatus
from app.models.team import Team, TeamMember

logger = logging.getLogger("htf.contracts")


# --- Person 2 Contract ---

async def is_active_member(db: AsyncSession, team_id: uuid.UUID, profile_id: uuid.UUID) -> bool:
    """Checks whether the profile is an active member of the given team."""
    # Check if profile is the leader directly on Team
    team_result = await db.execute(
        select(Team.id).where(
            Team.id == team_id,
            Team.leader_profile_id == profile_id,
        )
    )
    if team_result.scalar_one_or_none() is not None:
        return True

    # Check active member
    result = await db.execute(
        select(TeamMember.id).where(
            TeamMember.team_id == team_id,
            TeamMember.profile_id == profile_id,
            TeamMember.status == TeamMemberStatus.ACTIVE,
        )
    )
    return result.scalar_one_or_none() is not None


async def get_leader_profile_id(db: AsyncSession, team_id: uuid.UUID) -> Optional[uuid.UUID]:
    """Retrieves the profile ID of the team leader."""
    result = await db.execute(
        select(Team.leader_profile_id).where(Team.id == team_id)
    )
    return result.scalar_one_or_none()


# --- Person 4 Contract (Post-Commit Hooks) ---

async def on_shortlist_decided(application_id: uuid.UUID, result: str) -> None:
    """Hook invoked after a screening decision is committed.
    
    Stubbed until Person 4 implements notifications. Must remain idempotent.
    """
    logger.info(
        "HTF Person 4 Hook: on_shortlist_decided(application_id=%s, result=%s)",
        application_id,
        result,
    )


async def on_htf_confirmed(application_id: uuid.UUID) -> None:
    """Hook invoked after an HTF payment confirmation is committed.
    
    Stubbed until Person 4 implements passes and confirmation email. Must remain idempotent.
    """
    logger.info(
        "HTF Person 4 Hook: on_htf_confirmed(application_id=%s)",
        application_id,
    )
