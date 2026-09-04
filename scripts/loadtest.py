#!/usr/bin/env python3
"""Read-path load test against the API gateway.

Measures the console's hot endpoints (work queue, case workspace, dashboard) under concurrency
and prints throughput and latency percentiles. It deliberately exercises reads only: agent runs
are LLM-bound and cost money, so their latency lives in the eval harness, not here.

    uv run python scripts/loadtest.py --concurrency 20 --seconds 20
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import sys
import time
from collections import Counter, defaultdict

import httpx

GW = os.environ.get("GATEWAY_URL", "http://localhost:8000") + "/api/v1"


async def login(client: httpx.AsyncClient) -> str:
    r = await client.post(
        f"{GW}/iam/auth/login", json={"email": "ava@acme-demo.com", "password": "password"}
    )
    r.raise_for_status()
    return str(r.json()["access_token"])


async def worker(
    client: httpx.AsyncClient,
    token: str,
    case_ids: list[str],
    deadline: float,
    lat: dict[str, list[float]],
    codes: Counter[int],
    errors: Counter[str],
) -> None:
    h = {"authorization": f"Bearer {token}"}
    routes: list[tuple[str, str]] = [
        ("cases", f"{GW}/cases/cases?limit=50"),
        ("approvals", f"{GW}/cases/approvals"),
        ("dashboard", f"{GW}/analytics/metrics/dashboard?days=30"),
    ]
    while time.monotonic() < deadline:
        name, url = random.choice(routes)
        if case_ids and random.random() < 0.4:
            name, url = "case detail", f"{GW}/bff/case/{random.choice(case_ids)}"
        t0 = time.perf_counter()
        try:
            r = await client.get(url, headers=h)
            codes[r.status_code] += 1
            lat[name].append((time.perf_counter() - t0) * 1000)
        except Exception as e:
            errors[type(e).__name__] += 1


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--seconds", type=int, default=20)
    args = ap.parse_args()

    limits = httpx.Limits(
        max_connections=args.concurrency * 2, max_keepalive_connections=args.concurrency
    )
    async with httpx.AsyncClient(timeout=30, limits=limits) as client:
        token = await login(client)
        page = await client.get(
            f"{GW}/cases/cases?limit=25", headers={"authorization": f"Bearer {token}"}
        )
        case_ids = [c["id"] for c in page.json().get("items", [])]
        lat: dict[str, list[float]] = defaultdict(list)
        codes: Counter[int] = Counter()
        errors: Counter[str] = Counter()
        print(
            f"load: {args.concurrency} workers x {args.seconds}s against {GW} ({len(case_ids)} cases sampled)"
        )
        started = time.monotonic()
        await asyncio.gather(
            *(
                worker(client, token, case_ids, started + args.seconds, lat, codes, errors)
                for _ in range(args.concurrency)
            )
        )
        elapsed = time.monotonic() - started

    total = sum(len(v) for v in lat.values())
    throttled = codes.get(429, 0)
    print(f"\nrequests {total} in {elapsed:.1f}s  =  {total / elapsed:.1f} req/s")
    print(f"status codes: {dict(codes)}" + (f"  errors: {dict(errors)}" if errors else ""))
    if throttled:
        print(
            f"note: {throttled} responses were the gateway's own per-tenant rate limit "
            f"(RATE_LIMIT_PER_MINUTE); raise it to measure service capacity rather than the limiter."
        )
    width = max(len(k) for k in lat) if lat else 10
    print(f"\n  {'endpoint':<{width}}  {'n':>5}  {'p50':>8}  {'p95':>8}  {'p99':>8}  {'max':>8}")
    for name, values in sorted(lat.items()):
        print(
            f"  {name:<{width}}  {len(values):>5}  {statistics.median(values):>7.0f}ms  {pct(values, 95):>7.0f}ms  {pct(values, 99):>7.0f}ms  {max(values):>7.0f}ms"
        )
    failures = sum(n for c, n in codes.items() if c >= 400 and c != 429)
    if failures or errors:
        print(f"\n{failures} failed responses, {sum(errors.values())} transport errors")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
