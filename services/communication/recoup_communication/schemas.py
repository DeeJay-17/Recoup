from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class SendEmailRequest(BaseModel):
    tenant_id: uuid.UUID | None = None  # internal callers set it; user-facing route overrides
    case_id: uuid.UUID
    to: list[EmailStr] = Field(min_length=1)
    cc: list[EmailStr] = Field(default_factory=list)
    subject: str | None = None
    body_text: str | None = None
    template: str | None = None
    variables: dict[str, Any] = Field(default_factory=dict)
    invoice_refs: list[str] = Field(default_factory=list)
    in_reply_to: str | None = None  # our message_id to thread under
    idempotency_key: str = Field(min_length=8, max_length=128)
    approval_ref: str | None = None
    sent_by: str = "agent:communicator"


class MessageOut(BaseModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    case_id: uuid.UUID | None
    direction: str
    status: str
    message_id: str
    in_reply_to: str | None
    from_addr: str
    to_addrs: list[str]
    cc_addrs: list[str]
    subject: str
    body_text: str
    template: str | None
    attachments: list[dict[str, Any]]
    invoice_refs: list[str]
    link_method: str | None
    approval_ref: str | None
    sent_by: str | None
    error: str | None
    sent_at: datetime | None
    received_at: datetime | None
    created_at: datetime
    model_config = {"from_attributes": True}


class ThreadOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    case_id: uuid.UUID | None
    customer_ref: str | None
    subject: str
    invoice_refs: list[str]
    last_message_at: datetime | None
    created_at: datetime
    messages: list[MessageOut] = Field(default_factory=list)


class RenderRequest(BaseModel):
    template: str
    variables: dict[str, Any] = Field(default_factory=dict)


class RenderResponse(BaseModel):
    template: str
    subject: str
    body_text: str
    missing_variables: list[str]


class TemplateInfo(BaseModel):
    name: str
    description: str
    variables: list[str]


class PollResult(BaseModel):
    scanned: int
    ingested: int
    linked: int
    unlinked: int
