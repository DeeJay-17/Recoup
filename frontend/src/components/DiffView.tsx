import { diffLines } from "diff";
import clsx from "clsx";

/** Side-by-side-ish line diff of the agent's draft vs the human's edit. */
export function DiffView({ before, after }: { before: string; after: string }) {
  const parts = diffLines(before, after);
  if (before === after) return <p className="text-xs text-slate-400">No changes from the agent's draft.</p>;
  return (
    <pre className="max-h-64 overflow-auto rounded border border-slate-200 bg-white p-2 font-mono text-xs">
      {parts.map((p, i) => (
        <span
          key={i}
          className={clsx("block whitespace-pre-wrap", p.added && "bg-emerald-50 text-emerald-800", p.removed && "bg-rose-50 text-rose-800 line-through")}
        >
          {p.value.replace(/\n$/, "").split("\n").map((line, j) => (
            <span key={j} className="block">{p.added ? "+ " : p.removed ? "− " : "  "}{line}</span>
          ))}
        </span>
      ))}
    </pre>
  );
}
