import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/store/auth";

export interface LiveEvent {
  type: string;
  tenantid: string;
  time?: string;
  data: Record<string, unknown>;
}

const MAX_FEED = 100;

/** One WebSocket per app; invalidates queries as events arrive and keeps a small feed. */
export function useLiveEvents() {
  const token = useAuth((s) => s.token);
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [feed, setFeed] = useState<LiveEvent[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!token) return;
    let closed = false;
    let retry = 1000;
    const inv = (key: unknown[]) => void qc.invalidateQueries({ queryKey: key });
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}&replay=20`);
      wsRef.current = ws;
      ws.onopen = () => { setConnected(true); retry = 1000; };
      ws.onmessage = (m) => {
        let e: LiveEvent;
        try { e = JSON.parse(m.data as string) as LiveEvent; } catch { return; }
        if (e.type.startsWith("realtime.")) return;
        setFeed((f) => [e, ...f].slice(0, MAX_FEED));
        const caseId = typeof e.data?.case_id === "string" ? e.data.case_id : undefined;
        const runId = typeof e.data?.run_id === "string" ? e.data.run_id : undefined;
        if (e.type.startsWith("case.")) { inv(["cases"]); inv(["approvals"]); if (caseId) inv(["case", caseId]); }
        if (e.type.startsWith("agent.")) { inv(["runs"]); if (caseId) { inv(["caserun", caseId]); inv(["case", caseId]); } if (runId) inv(["run", runId]); }
        if (e.type.startsWith("comm.") && caseId) { inv(["threads", caseId]); inv(["case", caseId]); }
        if (e.type.startsWith("tool.") && caseId) { inv(["toolcalls", caseId]); inv(["case", caseId]); }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) { setTimeout(connect, retry); retry = Math.min(retry * 2, 15000); }
      };
      ws.onerror = () => ws.close();
    };
    connect();
    const ping = setInterval(() => { if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send("ping"); }, 20000);
    return () => { closed = true; clearInterval(ping); wsRef.current?.close(); };
  }, [token, qc]);

  return { connected, feed };
}
