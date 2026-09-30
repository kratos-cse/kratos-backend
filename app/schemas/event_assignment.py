import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class EventAssignmentCreate(BaseModel):
    admin_user_id: uuid.UUID


class EventAssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    assignment_id: uuid.UUID
    event_id: uuid.UUID
    admin_user_id: uuid.UUID
    email: Optional[str] = None
    name: Optional[str] = None
    role: str
    assigned_at: datetime
    assignment_type: str


class AssignableEventCoordinatorOut(BaseModel):
    admin_user_id: uuid.UUID
    user_id: uuid.UUID
    email: Optional[str] = None
    name: Optional[str] = None
    role: str
