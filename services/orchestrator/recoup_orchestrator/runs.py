"""Start / signal / inspect workflows. Used by the API and by the Kafka consumer."""

from __future__ import annotations

import uuid
from typing import Any

from recoup_common.errors import ConflictError, NotFoundError
from recoup_common.logging import get_logger
from temporalio.client import Client, WorkflowHandle
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from recoup_orchestrator.settings import Settings
from recoup_orchestrator.workflows.activities import RunParams

log = get_logger(__name__)


def workflow_id_for(case_id: uuid.UUID) -> str:
    return f"case-{case_id}"


class RunManager:
    def __init__(self, client: Client, settings: Settings) -> None:
        self.client = client
        self.s = settings

    async def start(
        self,
        *,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID,
        mode: str = "LIVE",
        requested_by: str = "system",
    ) -> RunParams:
        params = RunParams(
            run_id=uuid.uuid4(),
            case_id=case_id,
            tenant_id=tenant_id,
            mode=mode,
            max_steps=self.s.max_steps_per_run,
            max_tokens=self.s.max_tokens_per_run,
            customer_wait_hours=self.s.customer_wait_hours_default,
            approval_wait_days=self.s.approval_wait_days,
            requested_by=requested_by,
        )
        wf_id = workflow_id_for(case_id) if mode == "LIVE" else f"{mode.lower()}-{params.run_id}"
        try:
            await self.client.start_workflow(
                "CaseWorkflow", params, id=wf_id, task_queue=self.s.temporal_task_queue
            )
        except WorkflowAlreadyStartedError as e:
            raise ConflictError(
                f"a live run for case {case_id} is already in progress",
                details={"workflow_id": wf_id},
            ) from e
        log.info(
            "run.started",
            workflow_id=wf_id,
            run_id=str(params.run_id),
            case_id=str(case_id),
            mode=mode,
        )
        return params

    def handle(self, case_id: uuid.UUID) -> WorkflowHandle[Any, Any]:
        return self.client.get_workflow_handle(workflow_id_for(case_id))

    async def signal(self, case_id: uuid.UUID, name: str, payload: dict[str, Any]) -> bool:
        try:
            await self.handle(case_id).signal(name, payload)
            return True
        except RPCError as e:
            log.info("run.signal_skipped", case_id=str(case_id), signal=name, reason=str(e)[:120])
            return False

    async def status(self, case_id: uuid.UUID) -> dict[str, Any]:
        h = self.handle(case_id)
        try:
            desc = await h.describe()
        except RPCError as e:
            raise NotFoundError(f"no workflow for case {case_id}") from e
        out: dict[str, Any] = {
            "workflow_id": desc.id,
            "run_id": desc.run_id,
            "status": desc.status.name if desc.status else None,
            "start_time": desc.start_time.isoformat() if desc.start_time else None,
        }
        if desc.status and desc.status.name == "RUNNING":
            try:
                out["live"] = await h.query("status")
            except Exception as e:
                out["live_error"] = str(e)[:120]
        return out

    async def cancel(self, case_id: uuid.UUID, reason: str) -> bool:
        return await self.signal(case_id, "cancel_run", {"reason": reason})
