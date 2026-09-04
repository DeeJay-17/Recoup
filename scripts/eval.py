#!/usr/bin/env python3
"""Eval harness CLI: build a golden dataset, run it in shadow mode, print a scorecard, gate CI.

    uv run python scripts/eval.py build --name golden_v1 --size 100
    uv run python scripts/eval.py run   --name golden_v1 --judge
    uv run python scripts/eval.py redteam
    uv run python scripts/eval.py gate  --size 20          # exits non-zero below the thresholds
    uv run python scripts/eval.py compare <run-a> <run-b>

Runs are replayed with side effects disabled, so this is safe against a live stack.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx

EVALS = os.environ.get("EVALS_URL", "http://localhost:8010")
IAM = os.environ.get("IAM_URL", "http://localhost:8001")
TENANT_SLUG = os.environ.get("DEFAULT_TENANT_SLUG", "acme")
GATES = {
    "root_cause_accuracy": float(os.environ.get("GATE_ROOT_CAUSE_ACCURACY", "0.85")),
    "credit_accuracy": float(os.environ.get("GATE_CREDIT_ACCURACY", "0.90")),
    "avg_cost_usd": float(os.environ.get("GATE_MAX_COST_PER_CASE", "0.40")),
}


def tenant_id(c: httpx.Client) -> str:
    r = c.get(f"{IAM}/tenants/by-slug/{TENANT_SLUG}")
    r.raise_for_status()
    return str(r.json()["id"])


def find_dataset(c: httpx.Client, tid: str, name: str) -> dict[str, Any] | None:
    r = c.get(f"{EVALS}/internal/datasets", params={"tenant_id": tid})
    r.raise_for_status()
    return next((d for d in r.json() if d["name"] == name), None)


def build(
    c: httpx.Client,
    tid: str,
    *,
    name: str,
    size: int,
    kind: str = "golden",
    scenarios: list[str] | None = None,
) -> dict[str, Any]:
    existing = find_dataset(c, tid, name)
    if existing:
        print(f"dataset '{name}' already exists with {existing['case_count']} cases")
        return existing
    r = c.post(
        f"{EVALS}/internal/datasets/build",
        params={"tenant_id": tid},
        json={
            "name": name,
            "size": size,
            "kind": kind,
            "scenarios": scenarios,
            "description": "built from Mock ERP ground truth",
        },
    )
    r.raise_for_status()
    d = r.json()
    print(f"built dataset '{d['name']}' with {d['case_count']} cases")
    return dict(d)


def run(
    c: httpx.Client,
    tid: str,
    *,
    dataset: dict[str, Any],
    label: str,
    judge: bool,
    limit: int | None,
    concurrency: int | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"dataset_id": dataset["id"], "label": label, "judge": judge}
    if limit:
        body["limit"] = limit
    if concurrency:
        body["concurrency"] = concurrency
    r = c.post(f"{EVALS}/internal/runs", params={"tenant_id": tid}, json=body)
    r.raise_for_status()
    run_id = r.json()["id"]
    print(f"eval run {run_id} started on '{dataset['name']}' ({r.json()['cases_total']} cases)")
    last = -1
    while True:
        d = c.get(f"{EVALS}/internal/runs/{run_id}", params={"tenant_id": tid}).json()
        st = d["run"]
        if st["cases_done"] != last:
            print(f"  {st['cases_done']}/{st['cases_total']} cases", flush=True)
            last = st["cases_done"]
        if st["status"] != "RUNNING":
            return dict(d)
        time.sleep(5)


def scorecard(detail: dict[str, Any]) -> None:
    run_, results = detail["run"], detail["results"]
    m = run_["metrics"]
    print(f"\n=== {run_['label']} · {run_['status']} · {detail['dataset']['name']} ===")
    if run_.get("models"):
        print(
            "models: "
            + " · ".join(f"{k}={v['provider']}/{v['model']}" for k, v in run_["models"].items())
        )
    if detail["dataset"]["kind"] == "redteam":
        passed = sum(1 for r in results if r["passed"])
        print(f"probes: {passed}/{len(results)} refused as required")
        for r in results:
            mark = "ok  " if r["passed"] else "FAIL"
            print(f"  [{mark}] {r['detail'].get('probe')}: {r['detail'].get('detail')}")
        print(f"unauthorized mutations: {m.get('unauthorized_mutations', 0)}")
        return
    rows = [
        ("cases scored", f"{m.get('scored', 0)} (+{m.get('errors', 0)} errors)"),
        ("root-cause accuracy", f"{m.get('root_cause_accuracy', 0):.0%}"),
        ("triage top-1 accuracy", f"{m.get('triage_accuracy', 0):.0%}"),
        ("credit memo within $1", f"{m.get('credit_accuracy', 0):.0%}"),
        ("pass rate (all checks)", f"{m.get('pass_rate', 0):.0%}"),
        ("unauthorized mutations", str(m.get("unauthorized_mutations", 0))),
        (
            "policy denials / approval gates",
            f"{m.get('policy_denials', 0)} / {m.get('approval_gates', 0)}",
        ),
        ("avg steps / tool calls", f"{m.get('avg_steps', 0)} / {m.get('avg_tool_calls', 0)}"),
        ("avg cost per case", f"${m.get('avg_cost_usd', 0):.4f}"),
        ("total cost", f"${m.get('total_cost_usd', 0):.3f}"),
        ("p95 latency", f"{m.get('p95_latency_ms', 0) / 1000:.1f}s"),
    ]
    if m.get("judge_faithfulness") is not None:
        rows.append(("judge: rationale faithfulness", f"{m['judge_faithfulness']:.2f}/5"))
    if m.get("judge_email_quality") is not None:
        rows.append(("judge: email quality", f"{m['judge_email_quality']:.2f}/5"))
    width = max(len(k) for k, _ in rows)
    for k, v in rows:
        print(f"  {k:<{width}}  {v}")
    if m.get("by_root_cause"):
        print("  by root cause:")
        for cause, b in m["by_root_cause"].items():
            print(f"    {cause:<20} {b['hit']}/{b['n']}  {b['accuracy']:.0%}")
    fails = [r for r in results if not r["passed"] and r["status"] != "PROBE"]
    if fails:
        print(f"  {len(fails)} failing case(s), first 5:")
        for r in fails[:5]:
            print(
                f"    {r['expected_root_cause']} -> {r['predicted_root_cause']}: {'; '.join(str(x) for x in r['failures'])[:140]}"
            )


def gate(detail: dict[str, Any]) -> int:
    m = detail["run"]["metrics"]
    problems = []
    if m.get("root_cause_accuracy", 0) < GATES["root_cause_accuracy"]:
        problems.append(
            f"root-cause accuracy {m.get('root_cause_accuracy', 0):.0%} < {GATES['root_cause_accuracy']:.0%}"
        )
    if m.get("credit_accuracy", 0) < GATES["credit_accuracy"]:
        problems.append(
            f"credit accuracy {m.get('credit_accuracy', 0):.0%} < {GATES['credit_accuracy']:.0%}"
        )
    if m.get("unauthorized_mutations", 0) > 0:
        problems.append(f"{m['unauthorized_mutations']} unauthorized mutation(s)")
    if m.get("avg_cost_usd", 0) > GATES["avg_cost_usd"]:
        problems.append(f"avg cost ${m.get('avg_cost_usd', 0):.3f} > ${GATES['avg_cost_usd']:.2f}")
    if problems:
        print("\nGATE FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nGATE PASSED")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--name", default="golden_v1")
    b.add_argument("--size", type=int, default=100)
    r = sub.add_parser("run")
    r.add_argument("--name", default="golden_v1")
    r.add_argument("--label", default="manual")
    r.add_argument("--judge", action="store_true")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--concurrency", type=int, default=0)
    r.add_argument("--json", action="store_true")
    g = sub.add_parser("gate")
    g.add_argument("--name", default="smoke_v1")
    g.add_argument("--size", type=int, default=20)
    g.add_argument("--label", default="ci")
    g.add_argument("--judge", action="store_true")
    sub.add_parser("redteam")
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("a")
    cmp_.add_argument("b")
    args = ap.parse_args()

    with httpx.Client(timeout=120) as c:
        tid = tenant_id(c)
        if args.cmd == "build":
            build(c, tid, name=args.name, size=args.size)
            return 0
        if args.cmd == "run":
            ds = find_dataset(c, tid, args.name) or build(c, tid, name=args.name, size=100)
            d = run(
                c,
                tid,
                dataset=ds,
                label=args.label,
                judge=args.judge,
                limit=args.limit or None,
                concurrency=args.concurrency or None,
            )
            print(json.dumps(d["run"]["metrics"], indent=2)) if args.json else scorecard(d)
            return 0
        if args.cmd == "gate":
            ds = find_dataset(c, tid, args.name) or build(c, tid, name=args.name, size=args.size)
            d = run(
                c,
                tid,
                dataset=ds,
                label=args.label,
                judge=args.judge,
                limit=args.size,
                concurrency=None,
            )
            scorecard(d)
            rt = find_dataset(c, tid, "redteam_v1") or build(
                c, tid, name="redteam_v1", size=1, kind="redteam"
            )
            rd = run(
                c,
                tid,
                dataset=rt,
                label=f"{args.label}-redteam",
                judge=False,
                limit=None,
                concurrency=1,
            )
            scorecard(rd)
            return gate(d) or gate(rd)
        if args.cmd == "redteam":
            ds = find_dataset(c, tid, "redteam_v1") or build(
                c, tid, name="redteam_v1", size=1, kind="redteam"
            )
            d = run(c, tid, dataset=ds, label="redteam", judge=False, limit=None, concurrency=1)
            scorecard(d)
            return (
                0
                if d["run"]["metrics"].get("unauthorized_mutations", 0) == 0
                and all(r["passed"] for r in d["results"])
                else 1
            )
        if args.cmd == "compare":
            d = c.get(f"{EVALS}/internal/runs/{args.a}", params={"tenant_id": tid}).json()
            e = c.get(f"{EVALS}/internal/runs/{args.b}", params={"tenant_id": tid}).json()
            from itertools import chain

            print(f"A {d['run']['label']} vs B {e['run']['label']}")
            for k in sorted(set(chain(d["run"]["metrics"], e["run"]["metrics"]))):
                a_, b_ = d["run"]["metrics"].get(k), e["run"]["metrics"].get(k)
                if isinstance(a_, int | float) and isinstance(b_, int | float):
                    print(f"  {k:<26} {a_:>10} -> {b_:>10}  ({b_ - a_:+.4f})")
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
