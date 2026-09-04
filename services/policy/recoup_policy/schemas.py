from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from recoup_policy.engine import Decision, validate_rule


class PolicyVersionOut(BaseModel):
    id: uuid.UUID
    version: int
    rule: dict[str, Any]
    decision: Decision
    required_role: str | None
    reason: str | None
    created_by: str
    created_at: datetime
    model_config = {"from_attributes": True}


class PolicyOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str
    action_type: str
    priority: int
    enabled: bool
    current_version: int
    created_at: datetime
    updated_at: datetime
    current: PolicyVersionOut | None = None


class PolicyCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_]{3,64}$")
    description: str = ""
    action_type: str = Field(pattern=r"^([A-Z_]{3,40}|\*)$")
    priority: int = Field(default=100, ge=0, le=10000)
    enabled: bool = True
    rule: dict[str, Any]
    decision: Decision
    required_role: str | None = Field(default=None, pattern=r"^(analyst|manager|admin)$")
    reason: str | None = None

    @field_validator("rule")
    @classmethod
    def _rule_ok(cls, v: dict[str, Any]) -> dict[str, Any]:
        validate_rule(v)
        return v


class PolicyUpdate(BaseModel):
    """Metadata changes; rule/decision changes create a new version via PolicyVersionCreate."""

    description: str | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)
    enabled: bool | None = None


class PolicyVersionCreate(BaseModel):
    rule: dict[str, Any]
    decision: Decision
    required_role: str | None = Field(default=None, pattern=r"^(analyst|manager|admin)$")
    reason: str | None = None

    @field_validator("rule")
    @classmethod
    def _rule_ok(cls, v: dict[str, Any]) -> dict[str, Any]:
        validate_rule(v)
        return v


class EvaluateRequest(BaseModel):
    tenant_id: uuid.UUID
    case_id: uuid.UUID | None = None
    actor: str = "agent:unknown"
    action_type: str
    context: dict[str, Any] = Field(default_factory=dict)
    record: bool = True  # False for simulations / dry runs


class EvaluateResponse(BaseModel):
    decision: Decision
    required_role: str | None
    reason: str | None
    matched: list[dict[str, Any]]
    defaulted: bool
    evaluation_id: int | None = None


class SimulateRequest(BaseModel):
    """Dry-run a candidate rule against a batch of contexts (or past evaluations)."""

    rule: dict[str, Any]
    decision: Decision
    required_role: str | None = None
    contexts: list[dict[str, Any]] = Field(default_factory=list)
    use_history: bool = True
    history_limit: int = Field(default=200, ge=1, le=2000)

    @field_validator("rule")
    @classmethod
    def _rule_ok(cls, v: dict[str, Any]) -> dict[str, Any]:
        validate_rule(v)
        return v


class SimulateRow(BaseModel):
    context: dict[str, Any]
    current_decision: Decision
    candidate_decision: Decision
    changed: bool


class SimulateResponse(BaseModel):
    rows: list[SimulateRow]
    changed: int
    total: int


class EvaluationOut(BaseModel):
    id: int
    tenant_id: uuid.UUID
    case_id: uuid.UUID | None
    actor: str
    action_type: str
    context: dict[str, Any]
    decision: Decision
    required_role: str | None
    reason: str | None
    matched: list[Any]
    evaluated_at: datetime
    model_config = {"from_attributes": True}
