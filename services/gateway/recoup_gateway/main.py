"""Single ingress for the UI.

- Validates the JWT (except for login) and forwards it upstream unchanged.
- Routes ``/api/v1/<service>/...`` to the owning service.
- Serves a few BFF endpoints that stitch several calls into one page payload.
- Blocks ``/internal/*`` upstream paths from ever being reachable through the edge.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from recoup_common.auth import decode_token
from recoup_common.errors import DomainError, ForbiddenError, UnauthorizedError
from recoup_common.http import create_app

from recoup_gateway.ratelimit import RateLimiter
from recoup_gateway.settings import get_settings

settings = get_settings()

ROUTES: dict[str, str] = {
    "iam": settings.iam_url,
    "cases": settings.case_url,
    "erp": settings.mock_erp_url,  # dev only; exposes read endpoints for the Customer 360 page
}
PUBLIC_PATHS = {("iam", "/auth/login")}
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "host",
    "content-length",
}
_INTERNAL = re.compile(r"^/(internal|admin)(/|$)")


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.client = httpx.AsyncClient(timeout=settings.upstream_timeout_seconds)
    app.state.limiter = RateLimiter(settings.rate_limit_per_minute)
    try:
        yield
    finally:
        await app.state.client.aclose()


app = create_app(settings, title="Recoup API Gateway", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-request-id"],
)


def _authenticate(request: Request, service: str, path: str) -> str | None:
    if (service, path) in PUBLIC_PATHS:
        return None
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise UnauthorizedError("missing bearer token")
    principal = decode_token(settings, token)
    return str(principal.tenant_id)


async def _forward(request: Request, base: str, path: str) -> Response:
    client: httpx.AsyncClient = request.app.state.client
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    body = await request.body()
    try:
        upstream = await client.request(
            request.method,
            f"{base}{path}",
            params=request.query_params,
            headers=headers,
            content=body,
        )
    except httpx.HTTPError as e:
        return JSONResponse(status_code=502, content={"error": "bad_gateway", "message": str(e)})
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}
    return Response(
        content=upstream.content, status_code=upstream.status_code, headers=resp_headers
    )


# ---------- BFF ----------
async def _get_json(request: Request, url: str) -> Any:
    client: httpx.AsyncClient = request.app.state.client
    r = await client.get(url, headers={"authorization": request.headers.get("authorization", "")})
    if r.status_code >= 400:
        try:
            payload = r.json()
        except ValueError:
            payload = {"message": r.text}
        raise DomainErrorPassthrough(r.status_code, payload)
    return r.json()


class DomainErrorPassthrough(DomainError):
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        super().__init__(
            str(payload.get("message", "upstream error")), details=payload.get("details") or {}
        )
        self.status_code = status
        self.code = str(payload.get("error", "upstream_error"))


@app.get("/api/v1/bff/case/{case_id}", tags=["bff"])
async def bff_case(case_id: str, request: Request) -> dict[str, Any]:
    """Case workspace page = case + timeline + actions + invoice/customer snapshot from ERP."""
    _authenticate(request, "cases", "/cases")
    detail = await _get_json(request, f"{settings.case_url}/cases/{case_id}/detail")
    case = detail["case"]
    erp: dict[str, Any] = {"invoices": [], "customer": None}
    try:
        erp["customer"] = await _get_json(
            request, f"{settings.mock_erp_url}/customers/{case['customer_ref']}"
        )
        for ref in case["invoice_refs"]:
            erp["invoices"].append(
                await _get_json(request, f"{settings.mock_erp_url}/invoices/{ref}")
            )
    except DomainError:
        pass  # ERP unavailable — page still renders from case data
    return {**detail, "erp": erp}


@app.get("/api/v1/bff/me", tags=["bff"])
async def bff_me(request: Request) -> dict[str, Any]:
    _authenticate(request, "iam", "/auth/me")
    return await _get_json(request, f"{settings.iam_url}/auth/me")  # type: ignore[no-any-return]


# The catch-all proxy is registered LAST so the BFF routes above take precedence.
@app.api_route(
    "/api/v1/{service}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def proxy(service: str, path: str, request: Request) -> Response:
    base = ROUTES.get(service)
    if base is None:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "message": f"unknown service '{service}'"},
        )
    path = "/" + path
    if _INTERNAL.match(path):
        raise ForbiddenError("internal endpoints are not exposed through the gateway")
    tenant = _authenticate(request, service, path)
    if tenant and not request.app.state.limiter.allow(tenant):
        return JSONResponse(
            status_code=429, content={"error": "rate_limited", "message": "slow down"}
        )
    return await _forward(request, base, path)
