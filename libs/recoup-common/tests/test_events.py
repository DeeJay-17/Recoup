from recoup_common.events import DomainEvent, topic_for


def test_topic_mapping() -> None:
    assert topic_for("case.state_changed") == "recoup.case"
    assert topic_for("erp.invoice.overdue") == "recoup.erp"


def test_envelope_roundtrip() -> None:
    e = DomainEvent(type="case.created", source="case-service", tenantid="t1", data={"a": 1})
    raw = e.to_bytes()
    back = DomainEvent.from_bytes(raw)
    assert back.id == e.id
    assert back.data == {"a": 1}
    assert back.specversion == "1.0"
