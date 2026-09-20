from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID
from datetime import datetime

class RoleBase(BaseModel):
    name: str
    description: Optional[str] = None
    permissions: List[str] = Field(default_factory=list)

class RoleCreate(RoleBase):
    pass

class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[List[str]] = None

class RoleResponse(BaseModel):
    role_id: UUID = Field(alias="id")
    name: str
    description: Optional[str]
    permissions: List[str]
    
    class Config:
        from_attributes = True
        populate_by_name = True

class AdminUserBase(BaseModel):
    user_id: UUID
    role_id: UUID

class AdminUserUpdate(BaseModel):
    role_id: Optional[UUID] = None
    is_active: Optional[bool] = None

class AdminUserResponse(BaseModel):
    admin_user_id: UUID = Field(alias="id")
    user_id: UUID
    email: str
    full_name: str
    role: RoleResponse
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True