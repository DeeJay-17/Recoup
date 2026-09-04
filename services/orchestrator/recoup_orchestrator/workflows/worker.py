from __future__ import annotations

import asyncio
import contextlib

from recoup_common.logging import get_logger
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from recoup_orchestrator.settings import Settings
from recoup_orchestrator.workflows.activities import CaseActivities
from recoup_orchestrator.workflows.case_workflow import CaseWorkflow

log = get_logger(__name__)


async def connect(settings: Settings, *, attempts: int = 60) -> Client:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await Client.connect(
                settings.temporal_address,
                namespace=settings.temporal_namespace,
                data_converter=pydantic_data_converter,
            )
        except Exception as e:
            last = e
            if i % 5 == 0:
                log.warning("temporal.connect_retry", attempt=i, error=str(e)[:200])
            await asyncio.sleep(2)
    raise RuntimeError(f"could not connect to Temporal at {settings.temporal_address}: {last}")


class WorkerRunner:
    def __init__(self, client: Client, settings: Settings, activities: CaseActivities) -> None:
        self.worker = Worker(
            client,
            task_queue=settings.temporal_task_queue,
            workflows=[CaseWorkflow],
            activities=[
                activities.load_case_state,
                activities.supervisor_step,
                activities.run_specialist,
                activities.merge_signals,
                activities.record_wait,
                activities.finalize_case,
                activities.execute_action,
                activities.refresh_actions,
            ],
            max_concurrent_activities=8,
        )
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self.worker.run(), name="temporal-worker")
        log.info("temporal.worker_started")

    async def stop(self) -> None:
        await self.worker.shutdown()
        if self._task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
