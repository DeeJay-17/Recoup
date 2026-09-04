#!/usr/bin/env python3
"""Seed the running stack: demo tenant + users in IAM, scenario dataset in Mock ERP, one ingest.

Usage:  uv run python scripts/seed.py [--erp-seed 42] [--customers 60] [--invoices 400]
Reads IAM_URL / MOCK_ERP_URL / CASE_URL from the environment (.env), defaulting to localhost.
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

IAM = os.environ.get("IAM_URL", "http://localhost:8001")
ERP = os.environ.get("MOCK_ERP_URL", "http://localhost:8002")
CASE = os.environ.get("CASE_URL", "http://localhost:8003")

TENANT = {"slug": "acme", "name": "Acme Industrial Supply"}
USERS = [
    ("admin@acme-demo.com", "Platform Admin", ["admin"]),
    ("marcus@acme-demo.com", "Marcus Reyes", ["manager"]),
    ("ava@acme-demo.com", "Ava Chen", ["analyst"]),
    ("viewer@acme-demo.com", "Read Only", ["viewer"]),
]
PASSWORD = "password"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--erp-seed", type=int, default=42)
    ap.add_argument("--customers", type=int, default=60)
    ap.add_argument("--invoices", type=int, default=400)
    ap.add_argument("--no-ingest", action="store_true")
    args = ap.parse_args()

    with httpx.Client(timeout=120) as c:
        # --- IAM ---
        r = c.get(f"{IAM}/tenants/by-slug/{TENANT['slug']}")
        if r.status_code == 404:
            r = c.post(
                f"{IAM}/tenants",
                json={
                    **TENANT,
                    "admin_email": USERS[0][0],
                    "admin_password": PASSWORD,
                    "admin_full_name": USERS[0][1],
                },
            )
            r.raise_for_status()
            print(f"created tenant {TENANT['slug']}")
        else:
            r.raise_for_status()
            print(f"tenant {TENANT['slug']} exists")
        token = c.post(
            f"{IAM}/auth/login",
            json={"email": USERS[0][0], "password": PASSWORD, "tenant_slug": "acme"},
        )
        token.raise_for_status()
        auth = {"authorization": f"Bearer {token.json()['access_token']}"}
        for email, name, roles in USERS[1:]:
            r = c.post(
                f"{IAM}/users",
                headers=auth,
                json={"email": email, "full_name": name, "password": PASSWORD, "roles": roles},
            )
            if r.status_code == 201:
                print(f"created user {email} {roles}")
            elif r.status_code == 409:
                print(f"user {email} exists")
            else:
                r.raise_for_status()

        # --- Mock ERP ---
        r = c.post(
            f"{ERP}/admin/seed",
            json={"seed": args.erp_seed, "customers": args.customers, "invoices": args.invoices},
        )
        r.raise_for_status()
        info = r.json()
        print(
            f"seeded mock ERP: {info['customers']} customers, "
            f"{info['invoices']} invoices as of {info['as_of']}"
        )
        for k, v in info["scenario_counts"].items():
            print(f"   {k:<18} {v}")

        # --- Ingest ---
        if not args.no_ingest:
            r = c.post(f"{CASE}/internal/ingest")
            r.raise_for_status()
            print(f"ingestion: {r.json()}")
    print(
        "\nLogin at http://localhost:5173 (dev) or http://localhost:3000 (compose) "
        "with ava@acme-demo.com / password"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
