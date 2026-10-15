import uuid
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class ScreeningDecisionRequest(BaseModel):
    result: Literal["SHORTLISTED", "NOT_SHORTLISTED"]
    notes: Optional[str] = Field(default=None, max_length=5000)


class ScreeningResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    result: str
    notes: Optional[str] = None
    decided_by_admin_user_id: uuid.UUID
    decided_at: datetime
