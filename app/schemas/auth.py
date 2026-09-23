import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.schemas.profile import ProfileOut


class GoogleAuthRequest(BaseModel):
    id_token: str


class DevAuthRequest(BaseModel):
    email: EmailStr = "dev.tester@kratos.dev"
    name: str = "Dev Tester"
    is_admin: bool = False


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    is_admin_flagged: bool
    created_at: datetime
    last_login_at: datetime | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
    profile: ProfileOut


class MeResponse(BaseModel):
    user: UserOut
    profile: ProfileOut
    is_admin: bool
