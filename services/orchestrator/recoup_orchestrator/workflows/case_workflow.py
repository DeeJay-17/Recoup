"""Durable per-case workflow. Deterministic: all IO happens in activities.

Signals: customer_replied, action_decided, human_takeover, human_release.
Query:   status.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from recoup_orchestrator.agents.schemas import CaseOutcome, CaseState
    from recoup_orchestrator.workflows.activities import (
        FinalizeArgs,
        MergeArgs,
        RunParams,
        SpecialistArgs,
        StepResult,
        WaitArgs,
    )

_ACTIVITY = {
    "load_case_state": "load_case_state",
    "supervisor_step": "supervisor_step",
    "run_specialist": "run_specialist",
    "merge_signals": "merge_signals",
    "record_wait": "record_wait",
    "finalize_case": "finalize_case",
}
# Generous: sibling services may still be booting when a workflow starts.
RETRY_SHORT = RetryPolicy(
    maximum_attempts=10,
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
)
RETRY_LLM = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    non_retryable_error_types=["RepairExhausted", "IterationBudgetExceeded", "NotFoundError"],
)


@workflow.defn(name="CaseWorkflow")
class CaseWorkflow:
    def __init__(self) -> None:
        self.human_control = False
        self.pending: list[dict[str, Any]] = []
        self.phase = "starting"
        self.step_no = 0
        self.cancel_reason: str | None = None

    # ---------- signals / queries ----------
    @workflow.signal
    def customer_replied(self, payload: dict[str, Any]) -> None:
        self.pending.append({"type": "customer_reply", **payload})

    @workflow.signal
    def action_decided(self, payload: dict[str, Any]) -> None:
        self.pending.append({"type": "action_decided", **payload})

    @workflow.signal
    def human_takeover(self, payload: dict[str, Any] | None = None) -> None:
        self.human_control = True

    @workflow.signal
    def human_release(self, payload: dict[str, Any] | None = None) -> None:
        self.human_control = False

    @workflow.signal
    def cancel_run(self, payload: dict[str, Any] | None = None) -> None:
        self.cancel_reason = (payload or {}).get("reason") or "cancelled by user"

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "step_no": self.step_no,
            "human_control": self.human_control,
            "pending_signals": len(self.pending),
        }

    # ---------- main loop ----------
    @workflow.run
    async def run(self, params: RunParams) -> CaseOutcome:
        state: CaseState = await workflow.execute_activity(
            _ACTIVITY["load_case_state"],
            params,
            result_type=CaseState,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY_SHORT,
        )
        while state.step_no < params.max_steps:
            if self.cancel_reason:
                return await self._finalize(state, "CANCELLED", self.cancel_reason)
            if self.human_control:
                self.phase = "human_control"
                await workflow.execute_activity(
                    _ACTIVITY["record_wait"],
                    WaitArgs(state=state, reason="HUMAN_CONTROL"),
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=RETRY_SHORT,
                )
                await workflow.wait_condition(
                    lambda: not self.human_control or bool(self.cancel_reason)
                )
                continue
            self.phase = "supervisor"
            result: StepResult = await workflow.execute_activity(
                _ACTIVITY["supervisor_step"],
                state,
                result_type=StepResult,
                start_to_close_timeout=timedelta(minutes=5),
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=RETRY_LLM,
            )
            state = result.state
            self.step_no = state.step_no
            nxt = result.next
            if nxt == "WAIT_FOR_CUSTOMER":
                self.phase = "waiting_customer"
                hours = result.wait_timeout_hours or params.customer_wait_hours
                await workflow.execute_activity(
                    _ACTIVITY["record_wait"],
                    WaitArgs(state=state, reason="WAIT_FOR_CUSTOMER", hours=hours),
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=RETRY_SHORT,
                )
                signals = await self._wait_for_signals(
                    timedelta(hours=hours), on_timeout={"type": "timeout", "waited_hours": hours}
                )
                state = await workflow.execute_activity(
                    _ACTIVITY["merge_signals"],
                    MergeArgs(state=state, signals=signals),
                    result_type=CaseState,
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=RETRY_SHORT,
                )
                continue
            if nxt == "AWAIT_APPROVAL":
                self.phase = "awaiting_approval"
                await workflow.execute_activity(
                    _ACTIVITY["record_wait"],
                    WaitArgs(state=state, reason="AWAIT_APPROVAL"),
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=RETRY_SHORT,
                )
                signals = await self._wait_for_signals(
                    timedelta(days=params.approval_wait_days),
                    on_timeout={"type": "approval_timeout"},
                )
                state = await workflow.execute_activity(
                    _ACTIVITY["merge_signals"],
                    MergeArgs(state=state, signals=signals),
                    result_type=CaseState,
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=RETRY_SHORT,
                )
                continue
            if nxt in ("RESOLVED", "ESCALATED"):
                return await self._finalize(state, nxt, result.reason or nxt)
            self.phase = f"specialist:{nxt}"
            state = await workflow.execute_activity(
                _ACTIVITY["run_specialist"],
                SpecialistArgs(state=state, specialist=nxt),
                result_type=CaseState,
                start_to_close_timeout=timedelta(minutes=10),
                heartbeat_timeout=timedelta(minutes=3),
                retry_policy=RETRY_LLM,
            )
            self.step_no = state.step_no
            # signals that arrived while a specialist worked reach the next supervisor turn
            if self.pending:
                state = await workflow.execute_activity(
                    _ACTIVITY["merge_signals"],
                    MergeArgs(state=state, signals=self._drain()),
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=RETRY_SHORT,
                )
        return await self._finalize(state, "ESCALATED", "STEP_BUDGET_EXCEEDED")

    async def _wait_for_signals(
        self, limit: timedelta, *, on_timeout: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Temporal raises asyncio.TimeoutError when the condition is not met in time."""
        try:
            await workflow.wait_condition(
                lambda: bool(self.pending) or self.human_control or bool(self.cancel_reason),
                timeout=limit,
            )
        except TimeoutError:
            return [on_timeout]
        return self._drain() if self.pending else []

    def _drain(self) -> list[dict[str, Any]]:
        out, self.pending = self.pending, []
        return out

    async def _finalize(self, state: CaseState, status: str, reason: str) -> CaseOutcome:
        self.phase = f"finalizing:{status}"
        return await workflow.execute_activity(  # type: ignore[no-any-return]
            _ACTIVITY["finalize_case"],
            FinalizeArgs(state=state, status=status, reason=reason),
            result_type=CaseOutcome,
            start_to_close_timeout=timedelta(minutes=3),
            retry_policy=RETRY_SHORT,
        )
