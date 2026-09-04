from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from recoup_common.config import BaseServiceSettings
from recoup_common.errors import DomainError
from recoup_common.logging import configure_logging, get_logger
from recoup_common.tracing import current_trace_id, instrument_app, setup_tracing

log = get_logger(__name__)

LifespanFn = Callable[[FastAPI], AsyncIterator[None]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
        trace_id = current_trace_id()
        if trace_id:
            structlog.contextvars.bind_contextvars(trace_id=trace_id)
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response


def create_app(
    settings: BaseServiceSettings,
    *,
    title: str,
    lifespan: LifespanFn | None = None,
    version: str = "0.1.0",
) -> FastAPI:
    """Standard FastAPI app: logging, tracing, health endpoints, domain error mapping."""
    configure_logging(
        settings.log_level, json=settings.log_json, service_name=settings.service_name
    )
    setup_tracing(settings)

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("service.starting", service=settings.service_name, env=settings.environment)
        if lifespan is None:
            yield
        else:
            async for _ in lifespan(app):
                yield
        log.info("service.stopped", service=settings.service_name)

    app = FastAPI(title=title, version=version, lifespan=_lifespan)
    app.state.settings = settings
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": exc.message, "details": exc.details},
        )

    @app.get("/healthz", tags=["ops"], include_in_schema=False)
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "service": settings.service_name}

    @app.get("/readyz", tags=["ops"], include_in_schema=False)
    async def readyz() -> dict[str, Any]:
        db = getattr(app.state, "db", None)
        if db is not None:
            await db.ping()
        return {"status": "ready", "service": settings.service_name}

    instrument_app(app)
    return app
