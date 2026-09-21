from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class RoleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    permissions: List[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[List[str]] = None


class AdminUserCreate(BaseModel):
    user_id: UUID
    role_id: UUID


class AdminUserUpdate(BaseModel):
    role_id: Optional[UUID] = None
    is_active: Optional[bool] = None
