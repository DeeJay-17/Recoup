from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from recoup_common.db import utcnow


class DomainEvent(BaseModel):
    """CloudEvents 1.0 envelope with Recoup extensions (``tenantid``, ``traceparent``)."""

    specversion: Literal["1.0"] = "1.0"
    type: str
    source: str
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    time: datetime = Field(default_factory=utcnow)
    subject: str | None = None
    tenantid: str
    traceparent: str | None = None
    datacontenttype: str = "application/json"
    data: dict[str, Any] = Field(default_factory=dict)

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> DomainEvent:
        return cls.model_validate_json(raw)
