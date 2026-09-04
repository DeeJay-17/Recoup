from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from recoup_common.auth import Principal, Role, get_principal, issue_token, require_role
from recoup_common.db import Database
from recoup_common.errors import ForbiddenError, NotFoundError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_iam import service
from recoup_iam.models import Tenant, User
from recoup_iam.schemas import (
    LoginRequest,
    MeOut,
    TenantCreate,
    TenantOut,
    TokenResponse,
    UserCreate,
    UserOut,
)
from recoup_iam.settings import Settings

router = APIRouter()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.session() as session:
        yield session


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def _user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id,
        tenant_id=u.tenant_id,
        email=u.email,
        full_name=u.full_name,
        roles=[Role(r) for r in u.roles],
        is_active=u.is_active,
        created_at=u.created_at,
    )


@router.post("/auth/login", response_model=TokenResponse, tags=["auth"])
async def login(body: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    user, tenant = await service.authenticate(session, body.email, body.password, body.tenant_slug)
    principal = Principal(
        sub=user.id, tenant_id=tenant.id, email=user.email, roles=[Role(r) for r in user.roles]
    )
    token = issue_token(settings, principal)
    return TokenResponse(access_token=token, expires_in=settings.access_token_ttl_seconds)


@router.get("/auth/me", response_model=MeOut, tags=["auth"])
async def me(session: SessionDep, principal: Principal = Depends(get_principal)) -> MeOut:
    user = await session.get(User, principal.sub)
    if not user:
        raise NotFoundError("user not found")
    tenant = await service.get_tenant(session, user.tenant_id)
    return MeOut(
        id=user.id,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        email=user.email,
        full_name=user.full_name,
        roles=[Role(r) for r in user.roles],
    )


@router.post("/tenants", response_model=TenantOut, status_code=201, tags=["tenants"])
async def create_tenant(
    body: TenantCreate, session: SessionDep, settings: SettingsDep
) -> TenantOut:
    # Dev-only open endpoint. In prod this sits behind a platform-admin credential.
    if not settings.is_dev:
        raise ForbiddenError("tenant creation is disabled outside dev")
    tenant, _ = await service.create_tenant(session, body)
    return TenantOut(id=tenant.id, slug=tenant.slug, name=tenant.name, created_at=tenant.created_at)


@router.get("/tenants/by-slug/{slug}", response_model=TenantOut, tags=["tenants"])
async def tenant_by_slug(slug: str, session: SessionDep) -> TenantOut:
    tenant = await service.get_tenant_by_slug(session, slug)
    return TenantOut(id=tenant.id, slug=tenant.slug, name=tenant.name, created_at=tenant.created_at)


@router.get("/users", response_model=list[UserOut], tags=["users"])
async def list_users(
    session: SessionDep, principal: Principal = Depends(require_role(Role.MANAGER))
) -> list[UserOut]:
    rows = (
        await session.scalars(
            select(User).where(User.tenant_id == principal.tenant_id).order_by(User.created_at)
        )
    ).all()
    return [_user_out(u) for u in rows]


@router.post("/users", response_model=UserOut, status_code=201, tags=["users"])
async def create_user(
    body: UserCreate, session: SessionDep, principal: Principal = Depends(require_role(Role.ADMIN))
) -> UserOut:
    user = await service.create_user(session, principal.tenant_id, body)
    return _user_out(user)


@router.get("/tenants/{tenant_id}", response_model=TenantOut, tags=["tenants"])
async def get_tenant(
    tenant_id: str, session: SessionDep, principal: Principal = Depends(get_principal)
) -> TenantOut:
    if str(principal.tenant_id) != tenant_id:
        raise ForbiddenError("cross-tenant access denied")
    tenant = await session.get(Tenant, principal.tenant_id)
    if not tenant:
        raise NotFoundError("tenant not found")
    return TenantOut(id=tenant.id, slug=tenant.slug, name=tenant.name, created_at=tenant.created_at)
