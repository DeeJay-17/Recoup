import clsx from "clsx";
import type { EvalRunDetail } from "@/api/schemas";
import { Empty } from "./ui";

const pct = (v: unknown) => (typeof v === "number" ? `${Math.round(v * 100)}%` : "—");
const num = (v: unknown, d = 2) => (typeof v === "number" ? v.toFixed(d) : "—");

/** Targets from the project plan; the colour is the "would this ship" signal. */
const TARGETS: Record<string, number> = { root_cause_accuracy: 0.85, credit_accuracy: 0.9, pass_rate: 0.7 };

function Metric({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="rounded border border-slate-200 bg-white p-2">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className={clsx("text-lg font-semibold tabular-nums", ok === true && "text-emerald-700", ok === false && "text-rose-700")}>{value}</div>
    </div>
  );
}

export function EvalScorecard({ detail }: { detail: EvalRunDetail }) {
  const m = detail.run.metrics as Record<string, number | undefined>;
  const redteam = detail.dataset.kind === "redteam";
  if (redteam) {
    const passed = detail.results.filter((r) => r.passed).length;
    return (
      <div className="space-y-2">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
          <Metric label="probes refused" value={`${passed}/${detail.results.length}`} ok={passed === detail.results.length} />
          <Metric label="unauthorized mutations" value={String(m.unauthorized_mutations ?? 0)} ok={(m.unauthorized_mutations ?? 0) === 0} />
        </div>
        <ul className="divide-y divide-slate-100 text-sm">
          {detail.results.map((r) => (
            <li key={r.id} className="flex items-start gap-2 py-2">
              <span className={clsx("rounded px-1.5 py-0.5 text-xs font-medium", r.passed ? "bg-emerald-100 text-emerald-800" : "bg-rose-100 text-rose-800")}>
                {r.passed ? "refused" : "FAILED"}
              </span>
              <div>
                <div className="font-medium text-slate-900">{String((r.detail as Record<string, unknown>).probe)}</div>
                <div className="text-xs text-slate-500">{String((r.detail as Record<string, unknown>).description)}</div>
                <div className="text-xs text-slate-400">{String((r.detail as Record<string, unknown>).detail)}</div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (!m.cases) return <Empty>No metrics yet.</Empty>;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Metric label="root-cause accuracy" value={pct(m.root_cause_accuracy)} ok={(m.root_cause_accuracy ?? 0) >= TARGETS.root_cause_accuracy} />
        <Metric label="credit within $1" value={pct(m.credit_accuracy)} ok={(m.credit_accuracy ?? 0) >= TARGETS.credit_accuracy} />
        <Metric label="pass rate" value={pct(m.pass_rate)} ok={(m.pass_rate ?? 0) >= TARGETS.pass_rate} />
        <Metric label="unauthorized mutations" value={String(m.unauthorized_mutations ?? 0)} ok={(m.unauthorized_mutations ?? 0) === 0} />
        <Metric label="triage top-1" value={pct(m.triage_accuracy)} />
        <Metric label="avg cost / case" value={`$${num(m.avg_cost_usd, 4)}`} ok={(m.avg_cost_usd ?? 0) <= 0.4} />
        <Metric label="avg steps" value={num(m.avg_steps, 1)} />
        <Metric label="p95 latency" value={`${num((m.p95_latency_ms ?? 0) / 1000, 1)}s`} />
        {m.judge_faithfulness != null && <Metric label="judge: faithfulness" value={`${num(m.judge_faithfulness)}/5`} />}
        {m.judge_email_quality != null && <Metric label="judge: email quality" value={`${num(m.judge_email_quality)}/5`} />}
      </div>
      {m.by_root_cause && (
        <div className="text-xs text-slate-600">
          by root cause:{" "}
          {Object.entries(m.by_root_cause as unknown as Record<string, { n: number; hit: number; accuracy: number }>).map(([k, v]) => (
            <span key={k} className="mr-3 whitespace-nowrap">
              {k} <span className={v.accuracy >= 0.85 ? "text-emerald-700" : "text-slate-500"}>{v.hit}/{v.n}</span>
            </span>
          ))}
        </div>
      )}
      {/* Eight columns do not fit a half-width card: scroll the table, never the page. */}
      <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-xs [&_td]:pr-3 [&_th]:pr-3">
        <thead className="text-left text-slate-500">
          <tr><th className="py-1">expected</th><th>triage</th><th>confirmed</th><th className="text-right">credit Δ</th><th className="text-right">steps</th><th className="text-right">cost</th><th>outcome</th><th className="pr-0">failures</th></tr>
        </thead>
        <tbody>
          {detail.results.map((r) => (
            <tr key={r.id} className={clsx("border-t border-slate-100", !r.passed && "bg-rose-50/40")}>
              <td className="py-1">{r.expected_root_cause}</td>
              <td className={clsx(r.triage_root_cause !== r.expected_root_cause && "text-amber-700")}>{r.triage_root_cause ?? "—"}</td>
              <td className={clsx(r.predicted_root_cause !== r.expected_root_cause && "text-rose-700")}>{r.predicted_root_cause ?? "—"}</td>
              <td className="text-right tabular-nums">{r.credit_delta == null ? "—" : r.credit_delta.toFixed(2)}</td>
              <td className="text-right tabular-nums">{r.steps}</td>
              <td className="text-right tabular-nums">${r.cost_usd.toFixed(3)}</td>
              <td>{r.terminal_status ?? r.status}</td>
              <td className="pr-0 text-slate-500">{r.failures.map(String).join("; ").slice(0, 80)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  );
}
