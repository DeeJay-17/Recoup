from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field
from recoup_common.auth import Principal, Role, get_principal, require_role
from recoup_common.db import Database
from recoup_common.errors import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_knowledge import ingest, memory
from recoup_knowledge.models import CustomerMemory, Document
from recoup_knowledge.retrieval import Hit, hybrid_search
from recoup_knowledge.writer import Writer

router = APIRouter()
internal = APIRouter(prefix="/internal", tags=["internal"])


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
Viewer = Annotated[Principal, Depends(get_principal)]
Analyst = Annotated[Principal, Depends(require_role(Role.ANALYST))]


class DocumentIn(BaseModel):
    kind: str = Field(pattern=r"^(contract|sop|email|resolution|other)$")
    title: str = Field(max_length=200)
    text: str = Field(min_length=1)
    customer_ref: str | None = None
    source_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    customer_ref: str | None
    source_ref: str | None
    chunk_count: int
    metadata: dict[str, Any]
    created_at: str
    preview: str


class SearchIn(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    customer_ref: str | None = None
    kinds: list[str] | None = None
    k: int = Field(default=6, ge=1, le=30)


class HitOut(BaseModel):
    chunk_id: int
    document_id: str
    kind: str
    title: str
    customer_ref: str | None
    chunk_no: int
    content: str
    score: float
    sources: list[str]
    metadata: dict[str, Any]


class MemoryIn(BaseModel):
    fact: str = Field(min_length=8, max_length=300)
    category: str = Field(pattern=r"^(CONTACT|PREFERENCE|PATTERN|RISK)$")
    confidence: float = Field(default=0.8, ge=0, le=1)
    source_case_id: uuid.UUID | None = None


class MemoryOut(BaseModel):
    id: uuid.UUID
    customer_ref: str
    fact: str
    category: str
    confidence: float
    source_case_id: uuid.UUID | None
    source_ref: str | None
    created_by: str
    created_at: str


class ExtractIn(BaseModel):
    tenant_id: uuid.UUID
    case_id: uuid.UUID


def _doc_out(d: Document) -> DocumentOut:
    return DocumentOut(
        id=d.id,
        kind=d.kind,
        title=d.title,
        customer_ref=d.customer_ref,
        source_ref=d.source_ref,
        chunk_count=d.chunk_count,
        metadata=d.metadata_,
        created_at=d.created_at.isoformat(),
        preview=d.content_text[:240],
    )


def _mem_out(m: CustomerMemory) -> MemoryOut:
    return MemoryOut(
        id=m.id,
        customer_ref=m.customer_ref,
        fact=m.fact,
        category=m.category,
        confidence=float(m.confidence),
        source_case_id=m.source_case_id,
        source_ref=m.source_ref,
        created_by=m.created_by,
        created_at=m.created_at.isoformat(),
    )


def _hit_out(h: Hit) -> HitOut:
    return HitOut(
        chunk_id=h.chunk_id,
        document_id=h.document_id,
        kind=h.kind,
        title=h.title,
        customer_ref=h.customer_ref,
        chunk_no=h.chunk_no,
        content=h.content,
        score=h.score,
        sources=h.sources,
        metadata=h.metadata,
    )


async def _search(
    request: Request, session: AsyncSession, tenant_id: uuid.UUID, body: SearchIn
) -> list[HitOut]:
    st = request.app.state
    qv = await st.embedder.embed_query(body.query)
    hits = await hybrid_search(
        session,
        tenant_id=tenant_id,
        query=body.query,
        query_vec=qv,
        customer_ref=body.customer_ref,
        kinds=body.kinds,
        k=body.k,
        k_vector=st.settings.search_k_vector,
        k_fts=st.settings.search_k_fts,
    )
    return [_hit_out(h) for h in hits]


# ---------- user-facing ----------
@router.post("/documents", response_model=DocumentOut, status_code=201, tags=["documents"])
async def create_document(
    body: DocumentIn, session: SessionDep, principal: Analyst, request: Request
) -> DocumentOut:
    st = request.app.state
    d = await ingest.upsert_document(
        session,
        st.embedder,
        tenant_id=principal.tenant_id,
        kind=body.kind,
        title=body.title,
        text=body.text,
        customer_ref=body.customer_ref,
        source_ref=body.source_ref,
        metadata={**body.metadata, "uploaded_by": principal.email},
        chunk_chars=st.settings.chunk_chars,
        overlap=st.settings.chunk_overlap,
    )
    return _doc_out(d)


@router.post("/documents/upload", response_model=DocumentOut, status_code=201, tags=["documents"])
async def upload_document(
    session: SessionDep,
    principal: Analyst,
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form("other"),
    customer_ref: str | None = Form(None),
    title: str | None = Form(None),
) -> DocumentOut:
    raw = await file.read()
    ct = (file.content_type or "").lower()
    if ct == "application/pdf" or (file.filename or "").lower().endswith(".pdf"):
        import io

        import pdfplumber

        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            text = "\n\n".join(p.extract_text() or "" for p in pdf.pages[:60])
    else:
        text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        raise ValidationError("no extractable text in file")
    st = request.app.state
    d = await ingest.upsert_document(
        session,
        st.embedder,
        tenant_id=principal.tenant_id,
        kind=kind,
        title=title or file.filename or "upload",
        text=text,
        customer_ref=customer_ref,
        source_ref=f"file:{file.filename}",
        metadata={"filename": file.filename, "uploaded_by": principal.email},
        chunk_chars=st.settings.chunk_chars,
        overlap=st.settings.chunk_overlap,
    )
    return _doc_out(d)


@router.get("/documents", response_model=list[DocumentOut], tags=["documents"])
async def list_documents(
    session: SessionDep,
    principal: Viewer,
    customer_ref: str | None = None,
    kind: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[DocumentOut]:
    stmt = select(Document).where(Document.tenant_id == principal.tenant_id)
    if customer_ref:
        stmt = stmt.where(
            (Document.customer_ref == customer_ref) | (Document.customer_ref.is_(None))
        )
    if kind:
        stmt = stmt.where(Document.kind == kind)
    rows = await session.scalars(stmt.order_by(Document.created_at.desc()).limit(limit))
    return [_doc_out(d) for d in rows.all()]


@router.post("/search", response_model=list[HitOut], tags=["search"])
async def search(
    body: SearchIn, session: SessionDep, principal: Viewer, request: Request
) -> list[HitOut]:
    return await _search(request, session, principal.tenant_id, body)


@router.get("/customers/{customer_ref}/memory", response_model=list[MemoryOut], tags=["memory"])
async def get_memory(customer_ref: str, session: SessionDep, principal: Viewer) -> list[MemoryOut]:
    return [
        _mem_out(m) for m in await memory.list_memory(session, principal.tenant_id, customer_ref)
    ]


@router.post(
    "/customers/{customer_ref}/memory", response_model=MemoryOut, status_code=201, tags=["memory"]
)
async def add_memory(
    customer_ref: str, body: MemoryIn, session: SessionDep, principal: Analyst, request: Request
) -> MemoryOut:
    m = await memory.add_fact(
        session,
        request.app.state.embedder,
        tenant_id=principal.tenant_id,
        customer_ref=customer_ref,
        fact=body.fact,
        category=body.category,
        confidence=body.confidence,
        created_by=f"human:{principal.email}",
        source_case_id=body.source_case_id,
        source_ref="console",
    )
    return _mem_out(m)


@router.delete("/customers/{customer_ref}/memory/{memory_id}", status_code=204, tags=["memory"])
async def retire_memory(
    customer_ref: str, memory_id: uuid.UUID, session: SessionDep, principal: Analyst
) -> None:
    await memory.supersede(session, principal.tenant_id, memory_id, by=principal.email)


@router.get("/stats", tags=["ops"])
async def stats(session: SessionDep, principal: Viewer) -> dict[str, Any]:
    docs = (
        await session.execute(
            select(Document.kind, func.count())
            .where(Document.tenant_id == principal.tenant_id)
            .group_by(Document.kind)
        )
    ).all()
    mems = await session.scalar(
        select(func.count())
        .select_from(CustomerMemory)
        .where(
            CustomerMemory.tenant_id == principal.tenant_id, CustomerMemory.superseded_by.is_(None)
        )
    )
    return {"documents": {k: n for k, n in docs}, "memories": mems}


# ---------- internal: tools / orchestrator ----------
@internal.post("/search", response_model=list[HitOut])
async def search_internal(
    body: SearchIn, tenant_id: uuid.UUID, session: SessionDep, request: Request
) -> list[HitOut]:
    return await _search(request, session, tenant_id, body)


@internal.get("/customers/{customer_ref}/memory", response_model=list[MemoryOut])
async def memory_internal(
    customer_ref: str, tenant_id: uuid.UUID, session: SessionDep
) -> list[MemoryOut]:
    return [_mem_out(m) for m in await memory.list_memory(session, tenant_id, customer_ref)]


class InternalMemoryIn(MemoryIn):
    created_by: str = "agent"


@internal.post("/customers/{customer_ref}/memory", response_model=MemoryOut, status_code=201)
async def add_memory_internal(
    customer_ref: str,
    tenant_id: uuid.UUID,
    body: InternalMemoryIn,
    session: SessionDep,
    request: Request,
) -> MemoryOut:
    m = await memory.add_fact(
        session,
        request.app.state.embedder,
        tenant_id=tenant_id,
        customer_ref=customer_ref,
        fact=body.fact,
        category=body.category,
        confidence=body.confidence,
        created_by=body.created_by,
        source_case_id=body.source_case_id,
        source_ref="agent",
    )
    return _mem_out(m)


@internal.post("/memory/extract")
async def extract_internal(body: ExtractIn, request: Request) -> dict[str, Any]:
    writer: Writer = request.app.state.writer
    return await writer.write_for_case(body.tenant_id, body.case_id)


@internal.post("/seed")
async def seed_internal(tenant_id: uuid.UUID, request: Request) -> dict[str, Any]:
    writer: Writer = request.app.state.writer
    return await writer.seed(tenant_id)
