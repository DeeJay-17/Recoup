import { useState } from "react";
import clsx from "clsx";
import type { TimelineEvent } from "@/api/schemas";
import { relTime } from "@/lib/format";

const actorTone = { agent: "bg-indigo-500", human: "bg-emerald-500", system: "bg-slate-400" };

export function Timeline({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) return <p className="text-sm text-slate-500">No activity yet.</p>;
  return (
    <ol className="relative ml-2 border-l border-slate-200">
      {events.map((e) => (
        <TimelineItem key={e.id} e={e} />
      ))}
    </ol>
  );
}

function TimelineItem({ e }: { e: TimelineEvent }) {
  const [open, setOpen] = useState(false);
  const hasPayload = Object.keys(e.payload).length > 0;
  return (
    <li className="mb-4 ml-4">
      <span className={clsx("absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full ring-4 ring-white", actorTone[e.actor_type])} />
      <div className="flex items-baseline gap-2">
        <span className="text-xs uppercase tracking-wide text-slate-400">{e.kind.replace("_", " ")}</span>
        <span className="text-xs text-slate-400" title={e.occurred_at}>
          {relTime(e.occurred_at)}
        </span>
        <span className="text-xs text-slate-400">· {e.actor_id}</span>
      </div>
      <p className="text-sm text-slate-800">{e.title}</p>
      {hasPayload && (
        <button onClick={() => setOpen((o) => !o)} className="mt-1 text-xs text-slate-500 hover:underline">
          {open ? "hide details" : "details"}
        </button>
      )}
      {open && (
        <pre className="mt-1 max-h-64 overflow-auto rounded bg-slate-50 p-2 text-xs text-slate-700">
          {JSON.stringify(e.payload, null, 2)}
        </pre>
      )}
    </li>
  );
}
