from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from recoup_erp import ERPAdapter

from recoup_tool_gateway.clients import CaseClient, CommClient, PolicyClient
from recoup_tool_gateway.settings import Settings


@dataclass
class ToolContext:
    tenant_id: uuid.UUID
    case_id: uuid.UUID | None
    run_id: uuid.UUID | None
    actor: str
    settings: Settings
    erp: ERPAdapter
    cases: CaseClient
    policy: PolicyClient
    comm: CommClient
    _cache: dict[str, Any] = field(default_factory=dict)

    async def case(self) -> dict[str, Any] | None:
        """The current case (cached per invocation)."""
        if self.case_id is None:
            return None
        if "case" not in self._cache:
            self._cache["case"] = await self.cases.get_case(self.tenant_id, self.case_id)
        return dict(self._cache["case"])

    async def customer(self) -> dict[str, Any] | None:
        case = await self.case()
        if not case:
            return None
        if "customer" not in self._cache:
            self._cache["customer"] = (
                await self.erp.get_customer(case["customer_ref"])
            ).model_dump(mode="json")
        return dict(self._cache["customer"])
