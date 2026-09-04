import { useState } from "react";
import { useDashboard } from "@/api/hooks";
import { BarList, Heatmap, LineChart, StatTile } from "@/components/charts";
import { money } from "@/lib/viz";
import { Card, Empty, ErrorBox } from "@/components/ui";

const pct = (v: number) => `${Math.round(v * 100)}%`;
const hours = (h: number) => (h >= 48 ? `${(h / 24).toFixed(1)} d` : `${h.toFixed(1)} h`);
const RANGES = [7, 30, 90];

export function DashboardPage() {
  const [days, setDays] = useState(30);
  const [showTables, setShowTables] = useState(false);
  const q = useDashboard(days);

  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data) return <Empty>Loading metrics…</Empty>;
  const { summary: s, aging, trend, funnel, agents, escalations } = q.data;

  return (
    <div className="viz-root space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">Collections overview</h1>
        <span className="text-sm text-slate-500">Built from the event stream, not from another service's tables.</span>
        <div className="ml-auto flex items-center gap-1">
          {RANGES.map((d) => (
            <button key={d} onClick={() => setDays(d)}
                    className={d === days ? "rounded bg-slate-900 px-2.5 py-1 text-sm text-white" : "rounded px-2.5 py-1 text-sm text-slate-600 hover:bg-slate-100"}>
              {d}d
            </button>
          ))}
          <button onClick={() => setShowTables((v) => !v)} className="ml-2 rounded px-2.5 py-1 text-sm text-slate-600 hover:bg-slate-100">
            {showTables ? "hide tables" : "table view"}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <StatTile label="Open exposure" value={money(s.open_amount)} hint={`${s.open_cases} open cases`} />
        <StatTile label="Weighted age" value={`${s.dso_proxy_days} d`} hint="DSO proxy: amount-weighted days overdue" />
        <StatTile label="Closed" value={String(s.closed_cases)} hint={`median ${hours(s.median_hours_to_close)} to close`} />
        <StatTile label="Autonomy" value={pct(s.autonomy_rate)} hint={`${s.actions_proposed} actions proposed`} />
        <StatTile label="Approved unedited" value={pct(s.approval_without_edit_rate)} hint="of decided proposals" />
        <StatTile label="Escalation rate" value={pct(s.escalation_rate)} tone={s.escalation_rate > 0.5 ? "critical" : undefined} hint={`${pct(s.untouched_rate)} closed with no human touch`} />
        <StatTile label="Cost per closed case" value={`$${s.cost_per_closed_case.toFixed(3)}`} hint={`$${s.cost_usd.toFixed(2)} over ${s.agent_runs} runs`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Cases opened and closed">
          <LineChart
            names={["Opened", "Closed", "Escalated"]}
            data={trend.map((t) => ({ label: t.day, values: [t.opened, t.closed, t.escalated] }))}
          />
        </Card>
        <Card title="Open exposure by root cause and age">
          {aging.root_causes.length === 0 ? (
            <Empty>No open cases.</Empty>
          ) : (
            <Heatmap
              rows={aging.root_causes}
              cols={aging.buckets}
              cells={aging.cells.map((c) => ({ row: c.root_cause, col: c.bucket, value: c.amount, sub: `${c.cases} cases` }))}
            />
          )}
        </Card>
        <Card title={`Case funnel · last ${days} days`}>
          <BarList ordinal data={funnel.map((f) => ({ label: f.stage, value: f.cases, sub: `${pct(f.share)} of opened` }))} />
        </Card>
        <Card title="Why cases reach a human">
          {escalations.length === 0 ? (
            <Empty>No escalations in this window.</Empty>
          ) : (
            <BarList data={escalations.map((e) => ({ label: e.reason, value: e.cases, sub: `avg ${e.avg_steps} steps` }))} />
          )}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Agent step latency and errors">
          <table className="w-full text-xs">
            <thead className="text-left" style={{ color: "var(--text-muted)" }}>
              <tr><th className="py-1">agent</th><th className="text-right">steps</th><th className="text-right">errors</th><th className="text-right">avg</th><th className="text-right">p95</th><th className="text-right">tokens</th></tr>
            </thead>
            <tbody>
              {agents.by_agent.map((a) => (
                <tr key={a.agent} className="border-t border-slate-100">
                  <td className="py-1">{a.agent}</td>
                  <td className="text-right tabular-nums">{a.steps}</td>
                  <td className="text-right tabular-nums" style={{ color: a.errors ? "var(--status-critical)" : undefined }}>{a.errors}</td>
                  <td className="text-right tabular-nums">{(a.avg_latency_ms / 1000).toFixed(1)}s</td>
                  <td className="text-right tabular-nums">{(a.p95_latency_ms / 1000).toFixed(1)}s</td>
                  <td className="text-right tabular-nums">{a.tokens.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {agents.by_agent.length === 0 && <Empty>No agent steps yet.</Empty>}
        </Card>
        <Card title="Outcome by root cause">
          <table className="w-full text-xs">
            <thead className="text-left" style={{ color: "var(--text-muted)" }}>
              <tr><th className="py-1">root cause</th><th className="text-right">cases</th><th className="text-right">resolved</th><th className="text-right">escalated</th><th className="text-right">avg steps</th><th className="text-right">avg cost</th></tr>
            </thead>
            <tbody>
              {agents.by_root_cause.map((r) => (
                <tr key={r.root_cause} className="border-t border-slate-100">
                  <td className="py-1">{r.root_cause.replace("_", " ")}</td>
                  <td className="text-right tabular-nums">{r.cases}</td>
                  <td className="text-right tabular-nums">{r.resolved}</td>
                  <td className="text-right tabular-nums">{r.escalated}</td>
                  <td className="text-right tabular-nums">{r.avg_steps}</td>
                  <td className="text-right tabular-nums">${r.avg_cost.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {agents.by_root_cause.length === 0 && <Empty>No cases in this window.</Empty>}
        </Card>
      </div>

      {showTables && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Trend (table view)">
            <table className="w-full text-xs">
              <thead className="text-left" style={{ color: "var(--text-muted)" }}>
                <tr><th className="py-1">day</th><th className="text-right">opened</th><th className="text-right">closed</th><th className="text-right">escalated</th><th className="text-right">agent cost</th></tr>
              </thead>
              <tbody>
                {trend.map((t) => (
                  <tr key={t.day} className="border-t border-slate-100">
                    <td className="py-1">{t.day}</td>
                    <td className="text-right tabular-nums">{t.opened}</td>
                    <td className="text-right tabular-nums">{t.closed}</td>
                    <td className="text-right tabular-nums">{t.escalated}</td>
                    <td className="text-right tabular-nums">${t.cost.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <Card title="Aging (table view)">
            <table className="w-full text-xs">
              <thead className="text-left" style={{ color: "var(--text-muted)" }}>
                <tr><th className="py-1">root cause</th>{aging.buckets.map((b) => <th key={b} className="text-right">{b}</th>)}</tr>
              </thead>
              <tbody>
                {aging.root_causes.map((rc) => (
                  <tr key={rc} className="border-t border-slate-100">
                    <td className="py-1">{rc.replace("_", " ")}</td>
                    {aging.buckets.map((b) => {
                      const c = aging.cells.find((x) => x.root_cause === rc && x.bucket === b);
                      return <td key={b} className="text-right tabular-nums">{c && c.amount ? money(c.amount) : "—"}</td>;
                    })}
                  </tr>
                ))}
                <tr className="border-t border-slate-300 font-medium">
                  <td className="py-1">total</td>
                  {aging.totals.map((t) => <td key={t.bucket} className="text-right tabular-nums">{money(t.amount)}</td>)}
                </tr>
              </tbody>
            </table>
          </Card>
          <Card title="Tool usage">
            <table className="w-full text-xs">
              <thead className="text-left" style={{ color: "var(--text-muted)" }}>
                <tr><th className="py-1">tool</th><th className="text-right">calls</th><th className="text-right">ok</th><th className="text-right">gated</th><th className="text-right">avg latency</th></tr>
              </thead>
              <tbody>
                {agents.tools.map((t) => (
                  <tr key={t.tool} className="border-t border-slate-100">
                    <td className="py-1">{t.tool}</td>
                    <td className="text-right tabular-nums">{t.calls}</td>
                    <td className="text-right tabular-nums">{t.ok}</td>
                    <td className="text-right tabular-nums">{t.gated}</td>
                    <td className="text-right tabular-nums">{t.avg_latency_ms} ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      )}
    </div>
  );
}
