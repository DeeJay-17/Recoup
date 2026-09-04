import uuid
from datetime import UTC, datetime

from recoup_analytics.projector import _dec, _uuid
from recoup_common.events import DomainEvent


def test_uuid_and_decimal_coercion_never_raise() -> None:
    assert _uuid(None) is None and _uuid("nope") is None
    u = uuid.uuid4()
    assert _uuid(str(u)) == u
    assert _dec("12.50") is not None and _dec("") is None and _dec("abc") is None


def test_case_id_falls_back_to_subject() -> None:
    cid = str(uuid.uuid4())
    e = DomainEvent(
        type="case.created", source="case-service", tenantid=str(uuid.uuid4()), subject=cid, data={}
    )
    assert _uuid(e.data.get("case_id")) is None
    assert _uuid(e.subject) == uuid.UUID(cid)


def test_event_envelope_carries_time() -> None:
    e = DomainEvent(
        type="agent.run.completed",
        source="orchestrator",
        tenantid=str(uuid.uuid4()),
        data={"cost_usd": "0.12"},
    )
    assert isinstance(e.time, datetime) and e.time.tzinfo is not None
    assert _dec(e.data["cost_usd"]) is not None
    assert e.time <= datetime.now(UTC)
