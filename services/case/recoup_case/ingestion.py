"""Overdue-invoice ingestion: poll the ERP adapter and open cases for new overdue invoices.

Idempotent: ``case_invoices`` has a PK on (tenant_id, invoice_ref), so re-running never
duplicates a case. In prod this would be a Kafka consumer of ``erp.invoice.overdue``; for v1 the
poller lives here to keep the loop simple.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from datetime import date

import httpx
from recoup_common.db import Database, utcnow
from recoup_common.logging import get_logger
from recoup_erp import ERPAdapter, Invoice

from recoup_case import service
from recoup_case.models import IngestionRun
from recoup_case.schemas import CaseCreate, IngestResult
from recoup_case.settings import Settings

log = get_logger(__name__)


async def resolve_tenant_id(settings: Settings) -> uuid.UUID:
    if settings.default_tenant_id:
        return uuid.UUID(settings.default_tenant_id)
    async with httpx.AsyncClient(base_url=settings.iam_url, timeout=10) as client:
        r = await client.get(f"/tenants/by-slug/{settings.default_tenant_slug}")
        r.raise_for_status()
        return uuid.UUID(r.json()["id"])


class IngestionJob:
    def __init__(self, db: Database, erp: ERPAdapter, settings: Settings) -> None:
        self.db = db
        self.erp = erp
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._tenant_id: uuid.UUID | None = None
        self.last_result: IngestResult | None = None

    async def tenant_id(self) -> uuid.UUID:
        if self._tenant_id is None:
            self._tenant_id = await resolve_tenant_id(self.settings)
        return self._tenant_id

    async def run_once(self, *, as_of: date | None = None) -> IngestResult:
        t0 = time.perf_counter()
        tenant_id = await self.tenant_id()
        today = as_of or date.today()
        run = IngestionRun(tenant_id=tenant_id, started_at=utcnow())
        async with self.db.session() as session:
            session.add(run)
        scanned = created = skipped = 0
        try:
            invoices = await self.erp.list_open_invoices(
                overdue_days_gte=self.settings.ingest_overdue_days,
                limit=self.settings.ingest_max_per_run,
            )
            customer_names: dict[str, str] = {}
            for inv in invoices:
                scanned += 1
                async with self.db.session() as session:
                    if await service.find_case_for_invoice(session, tenant_id, inv.invoice_ref):
                        skipped += 1
                        continue
                    if inv.customer_ref not in customer_names:
                        with contextlib.suppress(Exception):
                            customer_names[inv.customer_ref] = (
                                await self.erp.get_customer(inv.customer_ref)
                            ).name
                    await service.create_case(
                        session,
                        tenant_id,
                        _to_case_create(inv, today, customer_names.get(inv.customer_ref)),
                    )
                    created += 1
        except Exception as e:
            log.error("ingestion.failed", error=str(e))
            async with self.db.session() as session:
                r = await session.get(IngestionRun, run.id)
                if r:
                    r.error = str(e)[:2000]
                    r.finished_at = utcnow()
                    r.scanned, r.created = scanned, created
            raise
        async with self.db.session() as session:
            r = await session.get(IngestionRun, run.id)
            if r:
                r.finished_at = utcnow()
                r.scanned, r.created = scanned, created
        result = IngestResult(
            tenant_id=tenant_id,
            scanned=scanned,
            created=created,
            skipped=skipped,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        self.last_result = result
        log.info("ingestion.done", **result.model_dump(mode="json"))
        return result

    async def _loop(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(Exception):  # already logged; retry next tick
                await self.run_once()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.ingest_interval_seconds
                )

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="ingestion")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


def _to_case_create(inv: Invoice, today: date, customer_name: str | None) -> CaseCreate:
    return CaseCreate(
        customer_ref=inv.customer_ref,
        customer_name=customer_name,
        invoice_refs=[inv.invoice_ref],
        amount_open=inv.amount_open,
        currency=inv.currency,
        days_overdue=inv.days_overdue(today),
        invoice_snapshot=inv.model_dump(mode="json", exclude={"lines"}),
    )
