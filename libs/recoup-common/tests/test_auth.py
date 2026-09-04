import uuid
from datetime import timedelta

import pytest
from recoup_common.auth import Principal, Role, decode_token, issue_token
from recoup_common.config import BaseServiceSettings
from recoup_common.errors import UnauthorizedError


def _settings() -> BaseServiceSettings:
    return BaseServiceSettings(jwt_secret="s3cret", _env_file=None)  # type: ignore[call-arg]


def test_roundtrip_token() -> None:
    s = _settings()
    p = Principal(
        sub=uuid.uuid4(), tenant_id=uuid.uuid4(), email="ava@acme-demo.com", roles=[Role.ANALYST]
    )
    token = issue_token(s, p)
    out = decode_token(s, token)
    assert out.sub == p.sub
    assert out.tenant_id == p.tenant_id
    assert out.roles == [Role.ANALYST]
    assert out.at_least(Role.VIEWER)
    assert not out.at_least(Role.MANAGER)


def test_expired_token_rejected() -> None:
    s = _settings()
    p = Principal(sub=uuid.uuid4(), tenant_id=uuid.uuid4(), email="x@y", roles=[Role.ADMIN])
    token = issue_token(s, p, ttl=timedelta(seconds=-10))
    with pytest.raises(UnauthorizedError):
        decode_token(s, token)


def test_wrong_secret_rejected() -> None:
    s = _settings()
    p = Principal(sub=uuid.uuid4(), tenant_id=uuid.uuid4(), email="x@y", roles=[Role.ADMIN])
    token = issue_token(s, p)
    other = BaseServiceSettings(jwt_secret="different", _env_file=None)  # type: ignore[call-arg]
    with pytest.raises(UnauthorizedError):
        decode_token(other, token)
