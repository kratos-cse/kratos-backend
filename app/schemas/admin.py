from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator


class RoleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    permissions: List[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[List[str]] = None


class AdminUserCreate(BaseModel):
    user_id: Optional[UUID] = None
    email: Optional[EmailStr] = None
    role_id: UUID

    @model_validator(mode="after")
    def user_id_xor_email(self) -> "AdminUserCreate":
        has_user = self.user_id is not None
        has_email = self.email is not None
        if has_user == has_email:
            raise ValueError("Provide exactly one of user_id or email.")
        return self


class AdminUserUpdate(BaseModel):
    role_id: Optional[UUID] = None
    is_active: Optional[bool] = None
