import { useState } from "react";
import clsx from "clsx";
import type { EmailMessage } from "@/api/schemas";
import { useCaseThreads, useSendEmail } from "@/api/hooks";
import { Button, Empty, ErrorBox } from "./ui";
import { relTime } from "@/lib/format";

export function EmailThreadPanel({ caseId, defaultTo }: { caseId: string; defaultTo?: string }) {
  const q = useCaseThreads(caseId);
  const send = useSendEmail(caseId);
  const [composing, setComposing] = useState(false);
  const [to, setTo] = useState(defaultTo ?? "");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const messages: EmailMessage[] = (q.data ?? []).flatMap((t) => t.messages);
  const lastInbound = [...messages].reverse().find((m) => m.direction === "IN");

  return (
    <div className="space-y-3">
      {q.error && <ErrorBox error={q.error} />}
      {messages.length === 0 && !composing && <Empty>No emails on this case yet.</Empty>}
      {messages.map((m) => (
        <MessageCard key={m.id} m={m} />
      ))}
      {composing ? (
        <form
          className="space-y-2 rounded border border-slate-200 bg-slate-50 p-3"
          onSubmit={(e) => {
            e.preventDefault();
            send.mutate(
              { to, subject, body_text: body, in_reply_to: lastInbound?.message_id ?? null },
              { onSuccess: () => { setComposing(false); setSubject(""); setBody(""); } },
            );
          }}
        >
          <input className="w-full rounded border border-slate-300 px-2 py-1 text-sm" placeholder="To" value={to} onChange={(e) => setTo(e.target.value)} />
          <input className="w-full rounded border border-slate-300 px-2 py-1 text-sm" placeholder="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
          <textarea className="h-32 w-full rounded border border-slate-300 p-2 text-sm" placeholder="Message" value={body} onChange={(e) => setBody(e.target.value)} />
          {send.error && <ErrorBox error={send.error} />}
          <div className="flex gap-2">
            <Button type="submit" disabled={send.isPending || !to || !subject || !body}>Send</Button>
            <Button type="button" variant="ghost" onClick={() => setComposing(false)}>Cancel</Button>
          </div>
        </form>
      ) : (
        <Button variant="secondary" onClick={() => setComposing(true)}>{lastInbound ? "Reply" : "Compose email"}</Button>
      )}
    </div>
  );
}

function MessageCard({ m }: { m: EmailMessage }) {
  const [open, setOpen] = useState(false);
  const inbound = m.direction === "IN";
  return (
    <div className={clsx("rounded-lg border p-3 text-sm", inbound ? "border-amber-200 bg-amber-50/40" : "border-slate-200 bg-white")}>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className={clsx("rounded px-1.5 py-0.5 text-xs font-medium", inbound ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-700")}>
          {inbound ? "← customer" : "→ sent"}
        </span>
        <span className="font-medium text-slate-900">{m.subject}</span>
        <span className="ml-auto text-xs text-slate-500" title={m.sent_at ?? m.received_at ?? m.created_at}>
          {relTime(m.sent_at ?? m.received_at ?? m.created_at)} · {m.status}
        </span>
      </div>
      <div className="mt-1 text-xs text-slate-500">
        {inbound ? `from ${m.from_addr}` : `to ${m.to_addrs.join(", ")}`}
        {m.template ? ` · template ${m.template}` : ""}
        {m.sent_by ? ` · ${m.sent_by}` : ""}
        {m.link_method && m.link_method !== "case" ? ` · linked by ${m.link_method}` : ""}
      </div>
      <pre className={clsx("mt-2 whitespace-pre-wrap font-sans text-slate-800", !open && "line-clamp-4")}>{m.body_text}</pre>
      {m.attachments.length > 0 && (
        <ul className="mt-2 text-xs text-slate-600">
          {m.attachments.map((a, i) => (
            <li key={i}>📎 {String(a.filename ?? "attachment")} {a.text_excerpt ? <span className="text-slate-400">· text extracted</span> : null}</li>
          ))}
        </ul>
      )}
      <button className="mt-1 text-xs text-slate-500 hover:underline" onClick={() => setOpen((o) => !o)}>{open ? "collapse" : "expand"}</button>
    </div>
  );
}
