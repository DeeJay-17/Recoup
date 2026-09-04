from recoup_common.events import DomainEvent
from recoup_realtime.hub import Hub


async def test_recent_filters_by_case() -> None:
    h = Hub(replay_buffer=5)
    for i in range(7):
        await h.publish(
            DomainEvent(
                type="case.state_changed",
                source="t",
                tenantid="t1",
                subject=f"c{i % 2}",
                data={"case_id": f"c{i % 2}"},
            )
        )
    assert len(h.recent("t1")) == 5
    assert all(e["data"]["case_id"] == "c1" for e in h.recent("t1", case_id="c1"))
    assert h.recent("other") == []
    assert h.stats()["connections"] == 0
