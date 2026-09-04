from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field
from recoup_common.auth import Role


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    tenant_slug: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class TenantCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{2,40}$")
    name: str
    admin_email: EmailStr
    admin_password: str = Field(min_length=6)
    admin_full_name: str = "Admin"


class TenantOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    created_at: datetime


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str = Field(min_length=6)
    roles: list[Role] = [Role.ANALYST]


class UserOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: str
    roles: list[Role]
    is_active: bool
    created_at: datetime


class MeOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_slug: str
    email: str
    full_name: str
    roles: list[Role]
