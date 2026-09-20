import uuid

from pydantic import BaseModel, ConfigDict, Field


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    contact_email: str | None = None
    phone: str | None = None
    college_name: str | None = None
    department: str | None = None
    year_of_study: str | None = None


class ProfileUpdateRequest(BaseModel):
    """All fields optional — PATCH semantics; only fields sent are updated."""

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    contact_email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    college_name: str | None = Field(default=None, max_length=200)
    department: str | None = Field(default=None, max_length=200)
    year_of_study: str | None = Field(default=None, max_length=50)
