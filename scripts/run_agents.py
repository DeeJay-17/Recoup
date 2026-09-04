#!/usr/bin/env python3
"""Start an agent run for every NEW case that has none (useful after enabling the orchestrator)."""

from __future__ import annotations

import os
import sys

import httpx

GW = os.environ.get("GATEWAY_URL", "http://localhost:8000") + "/api/v1"


def main() -> int:
    with httpx.Client(timeout=60) as c:
        tok = c.post(
            f"{GW}/iam/auth/login", json={"email": "ava@acme-demo.com", "password": "password"}
        ).json()["access_token"]
        h = {"authorization": f"Bearer {tok}"}
        cases = c.get(
            f"{GW}/cases/cases", params={"status": "NEW", "limit": 500}, headers=h
        ).json()["items"]
        started = skipped = 0
        for case in cases:
            r = c.post(f"{GW}/agents/runs", json={"case_id": case["id"]}, headers=h)
            if r.status_code == 201:
                started += 1
            else:
                skipped += 1
        print(
            f"NEW cases: {len(cases)}; runs started: {started}; "
            f"skipped (already running): {skipped}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
