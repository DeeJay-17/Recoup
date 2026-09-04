import { useState } from "react";
import clsx from "clsx";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import {
  useEvalDatasets,
  useEvalMutations,
  useEvalRun,
  useEvalRuns,
  useModelConfig,
  usePromptMutations,
  usePrompts,
  useRun,
  useRuns,
  useSaveModelConfig,
} from "@/api/hooks";
import { EvalScorecard } from "@/components/EvalScorecard";
import { hasRole, useAuth } from "@/store/auth";
import { Button, Card, Empty, ErrorBox } from "@/components/ui";
import { StepList } from "@/components/AgentRunCard";
import { runTone } from "@/lib/runTone";
import { relTime, shortId } from "@/lib/format";

type Tab = "runs" | "prompts" | "models" | "evals";

export function AgentsPage() {
  const search = useSearch({ from: "/app/agents" });
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>(search.run ? "runs" : "runs");
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold">Agents</h1>
        <div className="ml-auto flex gap-1">
          {(["runs", "prompts", "models", "evals"] as Tab[]).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={clsx("rounded px-2.5 py-1 text-sm capitalize", tab === t ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100")}>{t}</button>
          ))}
        </div>
      </div>
      {tab === "runs" && <RunsTab selected={search.run ?? null} onSelect={(id) => void navigate({ to: "/agents", search: { run: id ?? undefined } })} />}
      {tab === "prompts" && <PromptsTab />}
      {tab === "models" && <ModelsTab />}
      {tab === "evals" && <EvalsTab />}
    </div>
  );
}

function RunsTab({ selected, onSelect }: { selected: string | null; onSelect: (id: string | null) => void }) {
  const runs = useRuns();
  const detail = useRun(selected, true);
  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <div className="lg:col-span-2">
        <Card title={`Runs${runs.data ? ` · ${runs.data.length}` : ""}`}>
          {runs.error && <ErrorBox error={runs.error} />}
          {runs.data?.length === 0 && <Empty>No runs yet. Start one from a case page or run `make run-agents`.</Empty>}
          <ul className="divide-y divide-slate-100">
            {runs.data?.map((r) => (
              <li key={r.id}>
                <button className={clsx("flex w-full items-center gap-2 py-2 text-left", selected === r.id && "bg-slate-50")} onClick={() => onSelect(r.id)}>
                  <span className={clsx("rounded px-1.5 py-0.5 text-xs font-medium", runTone[r.status])}>{r.status}</span>
                  <span className="text-sm">{shortId(r.case_id)}</span>
                  <span className="text-xs text-slate-500">{r.phase}</span>
                  <span className="ml-auto text-xs text-slate-400">{r.steps} steps · ${r.cost_usd.toFixed(3)} · {relTime(r.started_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <div className="lg:col-span-3">
        {!selected && <Empty>Select a run to see its trace.</Empty>}
        {detail.error && <ErrorBox error={detail.error} />}
        {detail.data && (
          <Card
            title={<span>Run {shortId(detail.data.run.id)} · <Link to="/cases/$caseId" params={{ caseId: detail.data.run.case_id }} className="font-normal text-slate-500 hover:underline">case {shortId(detail.data.run.case_id)}</Link></span>}
            right={<span className={clsx("rounded px-1.5 py-0.5 text-xs", runTone[detail.data.run.status])}>{detail.data.run.status}</span>}
          >
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-600 md:grid-cols-4">
              <div><dt className="uppercase text-slate-400">Mode</dt><dd>{detail.data.run.mode}</dd></div>
              <div><dt className="uppercase text-slate-400">Tokens</dt><dd>{detail.data.run.tokens_in} in / {detail.data.run.tokens_out} out</dd></div>
              <div><dt className="uppercase text-slate-400">Cost</dt><dd>${detail.data.run.cost_usd.toFixed(4)}</dd></div>
              <div><dt className="uppercase text-slate-400">Prompts</dt><dd>{Object.entries(detail.data.run.prompt_bundle).map(([k, v]) => `${k} v${String(v)}`).join(", ")}</dd></div>
              <div className="col-span-2 md:col-span-4"><dt className="uppercase text-slate-400">Models</dt><dd>{Object.entries(detail.data.run.models).map(([t, m]) => { const mm = m as { provider: string; model: string }; return `${t}: ${mm.provider}/${mm.model}`; }).join(" · ")}</dd></div>
              {detail.data.workflow && <div className="col-span-2 md:col-span-4"><dt className="uppercase text-slate-400">Workflow</dt><dd><code>{JSON.stringify(detail.data.workflow)}</code></dd></div>}
            </dl>
            {detail.data.run.outcome && <p className="mt-2 rounded bg-slate-50 p-2 text-xs">Outcome: {String(detail.data.run.outcome.status)} — {String(detail.data.run.outcome.reason)}</p>}
            <StepList steps={detail.data.steps} />
          </Card>
        )}
      </div>
    </div>
  );
}

function PromptsTab() {
  const q = usePrompts();
  const m = usePromptMutations();
  const me = useAuth((s) => s.me);
  const canEdit = hasRole(me, "manager");
  const [name, setName] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [notes, setNotes] = useState("");
  const names = [...new Set((q.data ?? []).map((p) => p.name))];
  const versions = (q.data ?? []).filter((p) => p.name === name);
  const active = versions.find((v) => v.is_active);
  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <Card title="Prompts">
        <ul className="divide-y divide-slate-100 text-sm">
          {names.map((n) => (
            <li key={n}><button className={clsx("w-full py-2 text-left", name === n && "font-semibold")} onClick={() => { setName(n); const a = q.data?.find((p) => p.name === n && p.is_active); setDraft(a?.content ?? ""); }}>{n} <span className="text-xs text-slate-400">v{q.data?.find((p) => p.name === n && p.is_active)?.version}</span></button></li>
          ))}
        </ul>
      </Card>
      <div className="lg:col-span-4">
        {!name && <Empty>Select a prompt.</Empty>}
        {name && (
          <Card title={`${name} · active v${active?.version ?? "?"}`} right={<span className="text-xs text-slate-500">{versions.length} versions</span>}>
            <textarea className="h-96 w-full rounded border border-slate-300 p-2 font-mono text-xs" value={draft} onChange={(e) => setDraft(e.target.value)} readOnly={!canEdit} />
            {canEdit && (
              <div className="mt-2 flex flex-wrap gap-2">
                <input className="min-w-56 flex-1 rounded border border-slate-300 px-2 py-1 text-sm" placeholder="Change notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
                <Button onClick={() => m.addVersion.mutate({ name, content: draft, notes, activate: true })} disabled={m.addVersion.isPending || draft === active?.content}>Save & activate as v{(versions[0]?.version ?? 0) + 1}</Button>
                <Button variant="secondary" onClick={() => m.addVersion.mutate({ name, content: draft, notes, activate: false })} disabled={m.addVersion.isPending || draft === active?.content}>Save draft</Button>
              </div>
            )}
            {(m.addVersion.error || m.activate.error) && <div className="mt-2"><ErrorBox error={m.addVersion.error ?? m.activate.error} /></div>}
            <ul className="mt-3 divide-y divide-slate-100 text-xs">
              {versions.map((v) => (
                <li key={v.id} className="flex items-center gap-2 py-1">
                  <span className={clsx("rounded px-1.5 py-0.5", v.is_active ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-600")}>v{v.version}</span>
                  <span className="text-slate-600">{v.notes ?? ""}</span>
                  <span className="ml-auto text-slate-400">{v.created_by} · {relTime(v.created_at)}</span>
                  <button className="text-slate-500 hover:underline" onClick={() => setDraft(v.content)}>view</button>
                  {canEdit && !v.is_active && <button className="text-slate-700 hover:underline" onClick={() => m.activate.mutate({ name, version: v.version })}>activate</button>}
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </div>
  );
}

const PROVIDERS = ["", "google_genai", "openai", "anthropic", "azure_openai", "groq", "mistralai", "ollama", "openrouter", "heuristic"];

function ModelsTab() {
  const q = useModelConfig();
  const save = useSaveModelConfig();
  const me = useAuth((s) => s.me);
  const canEdit = hasRole(me, "manager");
  const [form, setForm] = useState<Record<string, Record<string, string>> | null>(null);
  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data) return <Empty>Loading…</Empty>;
  const f = form ?? Object.fromEntries(["fast", "strong"].map((t) => [t, { provider: q.data.overrides[t]?.provider ?? "", model: q.data.overrides[t]?.model ?? "", api_key: q.data.overrides[t]?.api_key ?? "", base_url: q.data.overrides[t]?.base_url ?? "" }]));
  const set = (tier: string, k: string, v: string) => setForm({ ...f, [tier]: { ...f[tier], [k]: v } });
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {["fast", "strong"].map((tier) => (
        <Card key={tier} title={`${tier} tier`} right={<span className="text-xs text-slate-500">effective: {q.data.effective[tier].provider}/{q.data.effective[tier].model}</span>}>
          <p className="text-xs text-slate-500">Default (env): {q.data.defaults[tier].provider}/{q.data.defaults[tier].model}. Leave fields empty to use the default. {tier === "fast" ? "Used for triage and tone." : "Used for supervisor, investigator, reconciler, negotiator."}</p>
          <div className="mt-2 grid gap-2 text-sm">
            <label>Provider
              <select className="mt-1 w-full rounded border border-slate-300 px-2 py-1" value={f[tier].provider} onChange={(e) => set(tier, "provider", e.target.value)} disabled={!canEdit}>
                {PROVIDERS.map((p) => <option key={p} value={p}>{p || "(default)"}</option>)}
              </select>
            </label>
            <label>Model<input className="mt-1 w-full rounded border border-slate-300 px-2 py-1" placeholder="e.g. gemini-2.5-flash" value={f[tier].model} onChange={(e) => set(tier, "model", e.target.value)} readOnly={!canEdit} /></label>
            <label>API key (tenant BYO)<input type="password" className="mt-1 w-full rounded border border-slate-300 px-2 py-1" placeholder={q.data.overrides[tier]?.api_key ? "•••• (set)" : "leave empty to use server key"} value={f[tier].api_key} onChange={(e) => set(tier, "api_key", e.target.value)} readOnly={!canEdit} /></label>
            <label>Base URL (OpenAI-compatible servers)<input className="mt-1 w-full rounded border border-slate-300 px-2 py-1" placeholder="http://ollama:11434/v1" value={f[tier].base_url} onChange={(e) => set(tier, "base_url", e.target.value)} readOnly={!canEdit} /></label>
          </div>
        </Card>
      ))}
      {canEdit && (
        <div className="md:col-span-2 flex items-center gap-2">
          <Button onClick={() => save.mutate(f)} disabled={save.isPending}>Save model configuration</Button>
          {save.error && <ErrorBox error={save.error} />}
          {save.isSuccess && <span className="text-sm text-emerald-700">Saved. New runs use the updated models.</span>}
        </div>
      )}
    </div>
  );
}

function EvalsTab() {
  const datasets = useEvalDatasets();
  const runs = useEvalRuns();
  const m = useEvalMutations();
  const me = useAuth((s) => s.me);
  const canRun = hasRole(me, "manager");
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useEvalRun(selected);
  const [name, setName] = useState("golden_v1");
  const [size, setSize] = useState(50);
  const [judge, setJudge] = useState(false);
  const [limit, setLimit] = useState(0);
  const [datasetId, setDatasetId] = useState<string>("");

  return (
    <div className="space-y-4">
      <Card title="Suites">
        <p className="text-xs text-slate-500">
          Runs replay real cases through the agents with side effects disabled, and score the outcome against the Mock ERP ground truth.
        </p>
        {canRun && (
          <div className="mt-3 flex flex-wrap items-end gap-2 text-sm">
            <label className="text-xs text-slate-600">Dataset name
              <input className="mt-0.5 block w-40 rounded border border-slate-300 px-2 py-1 text-sm" value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="text-xs text-slate-600">Cases
              <input type="number" className="mt-0.5 block w-20 rounded border border-slate-300 px-2 py-1 text-sm" value={size} onChange={(e) => setSize(Number(e.target.value))} />
            </label>
            <Button variant="secondary" onClick={() => m.build.mutate({ name, size, kind: "golden" })} disabled={m.build.isPending}>Build golden set</Button>
            <Button variant="secondary" onClick={() => m.build.mutate({ name: "redteam_v1", size: 1, kind: "redteam" })} disabled={m.build.isPending}>Build red-team set</Button>
          </div>
        )}
        <ul className="mt-3 divide-y divide-slate-100 text-sm">
          {datasets.data?.map((d) => (
            <li key={d.id} className="flex items-center gap-2 py-2">
              <span className={clsx("rounded px-1.5 py-0.5 text-xs", d.kind === "redteam" ? "bg-rose-100 text-rose-800" : "bg-slate-100 text-slate-700")}>{d.kind}</span>
              <span className="font-medium">{d.name}</span>
              <span className="text-xs text-slate-500">{d.case_count} cases · {relTime(d.created_at)}</span>
              {canRun && (
                <span className="ml-auto flex items-center gap-2">
                  <label className="flex items-center gap-1 text-xs text-slate-600">
                    <input type="checkbox" checked={judge && datasetId === d.id} onChange={(e) => { setDatasetId(d.id); setJudge(e.target.checked); }} /> judge
                  </label>
                  <input type="number" className="w-16 rounded border border-slate-300 px-1 py-0.5 text-xs" placeholder="all" value={datasetId === d.id && limit ? limit : ""} onChange={(e) => { setDatasetId(d.id); setLimit(Number(e.target.value)); }} />
                  <Button onClick={() => m.start.mutate({ dataset_id: d.id, label: `${d.name} @ ${new Date().toISOString().slice(11, 16)}`, judge: judge && datasetId === d.id, limit: datasetId === d.id && limit ? limit : undefined })} disabled={m.start.isPending}>
                    Run
                  </Button>
                </span>
              )}
            </li>
          ))}
        </ul>
        {datasets.data?.length === 0 && <Empty>No datasets yet. Build one from the seeded Mock ERP data.</Empty>}
        {(m.build.error || m.start.error) && <div className="mt-2"><ErrorBox error={m.build.error ?? m.start.error} /></div>}
      </Card>

      <div className="grid gap-4 lg:grid-cols-5">
        <div className="lg:col-span-2">
          <Card title="Eval runs">
            <ul className="divide-y divide-slate-100">
              {runs.data?.map((r) => (
                <li key={r.id}>
                  <button className={clsx("flex w-full items-center gap-2 py-2 text-left", selected === r.id && "bg-slate-50")} onClick={() => setSelected(r.id)}>
                    <span className={clsx("rounded px-1.5 py-0.5 text-xs font-medium", r.status === "COMPLETED" ? "bg-emerald-100 text-emerald-800" : r.status === "RUNNING" ? "bg-indigo-100 text-indigo-800" : "bg-rose-100 text-rose-800")}>{r.status}</span>
                    <span className="text-sm">{r.label}</span>
                    <span className="ml-auto text-xs text-slate-400">
                      {r.cases_done}/{r.cases_total}
                      {typeof r.metrics.root_cause_accuracy === "number" ? ` · ${Math.round((r.metrics.root_cause_accuracy as number) * 100)}%` : ""} · {relTime(r.started_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {runs.data?.length === 0 && <Empty>No eval runs yet.</Empty>}
          </Card>
        </div>
        <div className="lg:col-span-3">
          {!selected && <Empty>Select a run to see its scorecard.</Empty>}
          {detail.error && <ErrorBox error={detail.error} />}
          {detail.data && (
            <Card
              title={<span>{detail.data.run.label} <span className="font-normal text-slate-400">· {detail.data.dataset.name}</span></span>}
              right={<span className="text-xs text-slate-500">{Object.entries(detail.data.run.models).map(([t, v]) => { const mm = v as { provider: string; model: string }; return `${t}: ${mm.model}`; }).join(" · ")}</span>}
            >
              {detail.data.run.error && <ErrorBox error={detail.data.run.error} />}
              <EvalScorecard detail={detail.data} />
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
