from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine
from datetime import timedelta
from enum import StrEnum
from typing import Any

import jwt
from fastapi import Depends, Request
from pydantic import BaseModel, Field

from recoup_common.config import BaseServiceSettings
from recoup_common.db import utcnow
from recoup_common.errors import ForbiddenError, UnauthorizedError


class Role(StrEnum):
    ADMIN = "admin"
    MANAGER = "manager"
    ANALYST = "analyst"
    VIEWER = "viewer"


ROLE_RANK: dict[Role, int] = {Role.VIEWER: 0, Role.ANALYST: 1, Role.MANAGER: 2, Role.ADMIN: 3}


class Principal(BaseModel):
    """The authenticated subject carried in a JWT."""

    sub: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    roles: list[Role] = Field(default_factory=list)
    is_service: bool = False

    def has_role(self, role: Role) -> bool:
        return role in self.roles

    def at_least(self, role: Role) -> bool:
        """True if any of the principal's roles ranks >= ``role``."""
        return any(ROLE_RANK[r] >= ROLE_RANK[role] for r in self.roles)


def issue_token(
    settings: BaseServiceSettings, principal: Principal, *, ttl: timedelta | None = None
) -> str:
    now = utcnow()
    exp = now + (ttl or timedelta(seconds=settings.access_token_ttl_seconds))
    claims: dict[str, Any] = {
        "iss": settings.jwt_issuer,
        "sub": str(principal.sub),
        "tenant_id": str(principal.tenant_id),
        "email": principal.email,
        "roles": [r.value for r in principal.roles],
        "svc": principal.is_service,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(settings: BaseServiceSettings, token: str) -> Principal:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
        )
    except jwt.ExpiredSignatureError as e:
        raise UnauthorizedError("token expired") from e
    except jwt.PyJWTError as e:
        raise UnauthorizedError(f"invalid token: {e}") from e
    return Principal(
        sub=uuid.UUID(claims["sub"]),
        tenant_id=uuid.UUID(claims["tenant_id"]),
        email=claims.get("email", ""),
        roles=[Role(r) for r in claims.get("roles", [])],
        is_service=bool(claims.get("svc", False)),
    )


def _extract_bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise UnauthorizedError("missing bearer token")
    return token


async def get_principal(request: Request) -> Principal:
    """FastAPI dependency. Requires ``app.state.settings`` to be set."""
    settings: BaseServiceSettings = request.app.state.settings
    principal = decode_token(settings, _extract_bearer(request))
    request.state.principal = principal
    return principal


def require_role(minimum: Role) -> Callable[..., Coroutine[Any, Any, Principal]]:
    async def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.at_least(minimum):
            raise ForbiddenError(f"requires role >= {minimum.value}")
        return principal

    return _dep
