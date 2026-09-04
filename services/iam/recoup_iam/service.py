from __future__ import annotations

import uuid

from pwdlib import PasswordHash
from recoup_common.auth import Role
from recoup_common.errors import ConflictError, NotFoundError, UnauthorizedError
from recoup_common.events.outbox import enqueue_event
from recoup_common.events.topics import EventTypes
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_iam.models import Outbox, Tenant, User
from recoup_iam.schemas import TenantCreate, UserCreate

_hasher = PasswordHash.recommended()
SOURCE = "iam-service"


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return _hasher.verify(raw, hashed)


async def create_tenant(session: AsyncSession, data: TenantCreate) -> tuple[Tenant, User]:
    existing = await session.scalar(select(Tenant).where(Tenant.slug == data.slug))
    if existing:
        raise ConflictError(f"tenant slug '{data.slug}' already exists")
    tenant = Tenant(slug=data.slug, name=data.name)
    session.add(tenant)
    await session.flush()
    admin = User(
        tenant_id=tenant.id,
        email=data.admin_email.lower(),
        full_name=data.admin_full_name,
        password_hash=hash_password(data.admin_password),
        roles=[Role.ADMIN.value],
    )
    session.add(admin)
    await session.flush()
    enqueue_event(
        session,
        Outbox,
        source=SOURCE,
        tenant_id=tenant.id,
        aggregate_id=tenant.id,
        event_type=EventTypes.IAM_TENANT_CREATED,
        payload={"tenant_id": str(tenant.id), "slug": tenant.slug, "name": tenant.name},
    )
    enqueue_event(
        session,
        Outbox,
        source=SOURCE,
        tenant_id=tenant.id,
        aggregate_id=admin.id,
        event_type=EventTypes.IAM_USER_CREATED,
        payload={"user_id": str(admin.id), "email": admin.email, "roles": admin.roles},
    )
    return tenant, admin


async def create_user(session: AsyncSession, tenant_id: uuid.UUID, data: UserCreate) -> User:
    email = data.email.lower()
    dup = await session.scalar(select(User).where(User.tenant_id == tenant_id, User.email == email))
    if dup:
        raise ConflictError(f"user '{email}' already exists in tenant")
    user = User(
        tenant_id=tenant_id,
        email=email,
        full_name=data.full_name,
        password_hash=hash_password(data.password),
        roles=[r.value for r in data.roles],
    )
    session.add(user)
    await session.flush()
    enqueue_event(
        session,
        Outbox,
        source=SOURCE,
        tenant_id=tenant_id,
        aggregate_id=user.id,
        event_type=EventTypes.IAM_USER_CREATED,
        payload={"user_id": str(user.id), "email": user.email, "roles": user.roles},
    )
    return user


async def authenticate(
    session: AsyncSession, email: str, password: str, tenant_slug: str | None
) -> tuple[User, Tenant]:
    stmt = select(User, Tenant).join(Tenant, Tenant.id == User.tenant_id)
    stmt = stmt.where(User.email == email.lower(), User.is_active.is_(True))
    if tenant_slug:
        stmt = stmt.where(Tenant.slug == tenant_slug)
    rows = (await session.execute(stmt)).all()
    if not rows:
        raise UnauthorizedError("invalid credentials")
    if len(rows) > 1:
        raise UnauthorizedError("email exists in multiple tenants; specify tenant_slug")
    user, tenant = rows[0]
    if not verify_password(password, user.password_hash):
        raise UnauthorizedError("invalid credentials")
    return user, tenant


async def get_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if not tenant:
        raise NotFoundError("tenant not found")
    return tenant


async def get_tenant_by_slug(session: AsyncSession, slug: str) -> Tenant:
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    if not tenant:
        raise NotFoundError(f"tenant '{slug}' not found")
    return tenant
