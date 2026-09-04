"""Kafka -> Temporal bridge.

case.created starts a workflow; decisions, takeovers and customer replies become signals.
Idempotent: duplicate starts conflict harmlessly, duplicate signals are benign.
"""

from __future__ import annotations

import contextlib
import uuid

from recoup_common.errors import ConflictError
from recoup_common.events import DomainEvent
from recoup_common.events.topics import EventTypes
from recoup_common.logging import get_logger

from recoup_orchestrator.runs import RunManager
from recoup_orchestrator.settings import Settings

log = get_logger(__name__)


class EventBridge:
    def __init__(self, runs: RunManager, settings: Settings) -> None:
        self.runs = runs
        self.s = settings

    async def handle(self, event: DomainEvent) -> None:
        data = event.data
        case_id = data.get("case_id")
        if not case_id:
            return
        cid = uuid.UUID(case_id)
        t = event.type
        if t == EventTypes.CASE_CREATED:
            if not self.s.autostart_on_case_created:
                return
            with contextlib.suppress(ConflictError):
                await self.runs.start(
                    tenant_id=uuid.UUID(event.tenantid),
                    case_id=cid,
                    requested_by="event:case.created",
                )
        elif t in (
            EventTypes.CASE_ACTION_APPROVED,
            EventTypes.CASE_ACTION_EDITED,
            EventTypes.CASE_ACTION_REJECTED,
        ):
            await self.runs.signal(
                cid,
                "action_decided",
                {
                    "action_id": data.get("action_id"),
                    "action_type": data.get("action_type"),
                    "decision": t.rsplit(".", 1)[-1].upper(),
                    "by": data.get("by"),
                },
            )
        elif t == EventTypes.CASE_TAKEOVER:
            await self.runs.signal(
                cid, "human_takeover", {"by": data.get("by"), "reason": data.get("reason")}
            )
        elif t == EventTypes.CASE_RELEASED:
            await self.runs.signal(cid, "human_release", {"by": data.get("by")})
        elif t == EventTypes.COMM_EMAIL_RECEIVED:
            await self.runs.signal(
                cid,
                "customer_replied",
                {
                    "message_id": data.get("message_id"),
                    "from": data.get("from"),
                    "subject": data.get("subject"),
                    "link_method": data.get("link_method"),
                },
            )
