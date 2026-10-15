import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.htf_application import HtfApplicationStatus
from app.schemas.event_content import FieldResponseInput


class HtfApplicationCreate(BaseModel):
    """Empty payload for creating an HTF application."""
    pass


class HtfApplicationUpdate(BaseModel):
    """Update payload for updating draft application responses."""
    responses: list[FieldResponseInput] = Field(default_factory=list)


class HtfApplicationOut(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    team_id: uuid.UUID
    status: HtfApplicationStatus
    submitted_at: Optional[datetime] = None
    responses: dict[str, Any] = Field(default_factory=dict)
    is_editable: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
