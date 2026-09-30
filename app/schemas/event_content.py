import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ContentSectionType,
    RegistrationFieldScope,
    RegistrationFieldSource,
    RegistrationFieldType,
)


class ContentSectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    content: str
    section_type: ContentSectionType
    display_order: int
    is_visible: bool


class CoordinatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    contact: str
    role: str | None = None
    display_order: int


class RegistrationFieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scope: RegistrationFieldScope
    field_key: str
    label: str
    field_type: RegistrationFieldType
    required: bool
    is_visible: bool
    placeholder: str | None = None
    help_text: str | None = None
    display_order: int
    options: dict[str, Any] | list[Any] | None = None
    source: RegistrationFieldSource
    profile_field_key: str | None = None


class RegistrationFormOut(BaseModel):
    registration_fields: list[RegistrationFieldOut] = []
    team_member_fields: list[RegistrationFieldOut] = []


class FieldResponseInput(BaseModel):
    field_id: uuid.UUID
    value: Any = None


class FieldResponseOut(BaseModel):
    field_id: uuid.UUID
    field_key: str | None = None
    label: str | None = None
    value: Any = None


class ContentSectionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = ""
    section_type: ContentSectionType = ContentSectionType.CUSTOM
    display_order: int = 0
    is_visible: bool = True


class ContentSectionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = None
    section_type: ContentSectionType | None = None
    display_order: int | None = None
    is_visible: bool | None = None


class CoordinatorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    contact: str = Field(min_length=1, max_length=255)
    role: str | None = Field(default=None, max_length=200)
    display_order: int = 0


class CoordinatorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    contact: str | None = Field(default=None, min_length=1, max_length=255)
    role: str | None = Field(default=None, max_length=200)
    display_order: int | None = None


class RegistrationFieldCreate(BaseModel):
    scope: RegistrationFieldScope
    field_key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=300)
    field_type: RegistrationFieldType
    required: bool = False
    is_visible: bool = True
    placeholder: str | None = Field(default=None, max_length=300)
    help_text: str | None = Field(default=None, max_length=500)
    display_order: int = 0
    options: dict[str, Any] | list[Any] | None = None
    source: RegistrationFieldSource = RegistrationFieldSource.CUSTOM
    profile_field_key: str | None = Field(default=None, max_length=50)


class RegistrationFieldUpdate(BaseModel):
    field_key: str | None = Field(default=None, min_length=1, max_length=100)
    label: str | None = Field(default=None, min_length=1, max_length=300)
    field_type: RegistrationFieldType | None = None
    required: bool | None = None
    is_visible: bool | None = None
    placeholder: str | None = Field(default=None, max_length=300)
    help_text: str | None = Field(default=None, max_length=500)
    display_order: int | None = None
    options: dict[str, Any] | list[Any] | None = None
    source: RegistrationFieldSource | None = None
    profile_field_key: str | None = Field(default=None, max_length=50)


class ReorderBody(BaseModel):
    ordered_ids: list[uuid.UUID]
