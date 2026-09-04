from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI
from recoup_common.db import Database
from recoup_common.http import create_app
from recoup_common.logging import get_logger
from recoup_llm import LLMSettings, build_router

from recoup_evals import judge  # noqa: F401  (registers the heuristic judge)
from recoup_evals.clients import CaseClient, ErpClient, OrchestratorClient, ToolGatewayClient
from recoup_evals.routes import internal, router
from recoup_evals.runner import Harness
from recoup_evals.settings import get_settings

log = get_logger(__name__)
settings = get_settings()
llm_settings = LLMSettings()


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db = Database(settings.database_url, schema=settings.db_schema, pool_size=settings.db_pool_size)
    erp = ErpClient(settings.mock_erp_url)
    cases = CaseClient(settings.case_url)
    orch = OrchestratorClient(settings.orchestrator_url)
    gateway = ToolGatewayClient(settings.tool_gateway_url)
    router_ = build_router(llm_settings)
    harness = Harness(db, settings, router_, erp, cases, orch, gateway)
    app.state.db, app.state.harness = db, harness
    log.info(
        "evals.ready",
        judge_model=router_.describe()["strong"],
        concurrency=settings.run_concurrency,
    )
    try:
        yield
    finally:
        await harness.stop()
        for c in (erp, cases, orch, gateway):
            await c.aclose()
        await db.dispose()


app = create_app(settings, title="Recoup Eval Service", lifespan=lifespan)
app.include_router(router)
app.include_router(internal)
