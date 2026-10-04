import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.htf_application import HtfApplicationStatus


class JourneyItem(BaseModel):
    key: str
    status: str  # COMPLETE | IN_PROGRESS | LOCKED | NOT_APPLICABLE


class NextAction(BaseModel):
    type: str
    title: str
    description: str
    action: Optional[str] = None
    deadline: Optional[datetime] = None


class HtfDashboardOut(BaseModel):
    event: Optional[dict[str, Any]] = None
    profile: Optional[dict[str, Any]] = None
    team: Optional[dict[str, Any]] = None
    application: Optional[dict[str, Any]] = None
    submission: Optional[dict[str, Any]] = None
    screening: Optional[dict[str, Any]] = None
    payment: Optional[dict[str, Any]] = None
    confirmation: Optional[dict[str, Any]] = None
    journey: list[JourneyItem] = Field(default_factory=list)
    next_action: NextAction
    deadlines: dict[str, Any] = Field(default_factory=dict)
    announcements: list[dict[str, Any]] = Field(default_factory=list)


class HtfJourneyOut(BaseModel):
    journey: list[JourneyItem] = Field(default_factory=list)
    next_action: NextAction
