import { useState } from "react";
import type { ReactNode } from "react";

import { ORD, SEQ, SERIES, barPath, fitText, money, tick, useMeasure } from "@/lib/viz";

function Tooltip({ x, y, children }: { x: number; y: number; children: ReactNode }) {
  return (
    <div
      className="pointer-events-none absolute z-10 rounded border border-slate-200 bg-white px-2 py-1 text-xs shadow-sm"
      style={{ left: Math.max(0, x - 60), top: Math.max(0, y - 56) }}
    >
      {children}
    </div>
  );
}

export function Legend({ names, colors = SERIES }: { names: string[]; colors?: string[] }) {
  if (names.length < 2) return null;
  return (
    <ul className="mb-1 flex flex-wrap gap-3 text-xs" style={{ color: "var(--text-secondary)" }}>
      {names.map((n, i) => (
        <li key={n} className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 rounded" style={{ background: colors[i % colors.length] }} />
          {n}
        </li>
      ))}
    </ul>
  );
}

export interface Point { label: string; values: number[] }

/** Multi-series line with a crosshair tooltip, a legend and direct end labels. */
export function LineChart({ data, names, height = 200, valueFmt = tick }: { data: Point[]; names: string[]; height?: number; valueFmt?: (n: number) => string }) {
  const { ref, width } = useMeasure<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 40, r: 54, t: 10, b: 22 };
  const w = Math.max(320, width);
  const iw = w - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const max = Math.max(1, ...data.flatMap((d) => d.values));
  const x = (i: number) => pad.l + (data.length < 2 ? iw / 2 : (i / (data.length - 1)) * iw);
  const y = (v: number) => pad.t + ih - (v / max) * ih;
  const ticks = [0, max / 2, max];

  return (
    <div className="relative w-full overflow-hidden" ref={ref}>
      <Legend names={names} />
      <svg width={w} height={height} role="img" aria-label={`${names.join(", ")} over time`}
           onMouseLeave={() => setHover(null)}
           onMouseMove={(e) => {
             const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
             const i = Math.round(((e.clientX - rect.left - pad.l) / iw) * (data.length - 1));
             setHover(i >= 0 && i < data.length ? i : null);
           }}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={pad.l + iw} y1={y(t)} y2={y(t)} stroke="var(--grid)" strokeWidth={1} />
            <text x={pad.l - 6} y={y(t) + 3} textAnchor="end" fontSize={10} fill="var(--text-muted)">{valueFmt(t)}</text>
          </g>
        ))}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={pad.t + ih} stroke="var(--axis)" strokeWidth={1} />}
        {names.map((_, s) => (
          <path key={s} fill="none" stroke={SERIES[s % SERIES.length]} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"
                d={data.map((d, i) => `${i ? "L" : "M"}${x(i)},${y(d.values[s] ?? 0)}`).join(" ")} />
        ))}
        {names.map((n, s) => {
          const last = data[data.length - 1];
          if (!last) return null;
          return (
            <g key={`end-${n}`}>
              <circle cx={x(data.length - 1)} cy={y(last.values[s] ?? 0)} r={4}
                      fill={SERIES[s % SERIES.length]} stroke="var(--surface-1)" strokeWidth={2} />
              <text x={x(data.length - 1) + 8} y={y(last.values[s] ?? 0) + 3} fontSize={10} fill="var(--text-secondary)">
                {valueFmt(last.values[s] ?? 0)}
              </text>
            </g>
          );
        })}
        <line x1={pad.l} x2={pad.l + iw} y1={pad.t + ih} y2={pad.t + ih} stroke="var(--axis)" strokeWidth={1} />
        {data.map((d, i) =>
          i % Math.ceil(data.length / 6) === 0 ? (
            <text key={d.label} x={x(i)} y={height - 6} fontSize={10} textAnchor="middle" fill="var(--text-muted)">
              {d.label.slice(5)}
            </text>
          ) : null,
        )}
      </svg>
      {hover !== null && data[hover] && (
        <Tooltip x={x(hover)} y={pad.t}>
          <div className="font-medium" style={{ color: "var(--text-primary)" }}>{data[hover].label}</div>
          {names.map((n, s) => (
            <div key={n} className="flex items-center gap-1.5" style={{ color: "var(--text-secondary)" }}>
              <span className="inline-block h-0.5 w-3 rounded" style={{ background: SERIES[s % SERIES.length] }} />
              {n}: {valueFmt(data[hover].values[s] ?? 0)}
            </div>
          ))}
        </Tooltip>
      )}
    </div>
  );
}

export interface BarDatum { label: string; value: number; sub?: string }

/** Horizontal bars for ranked magnitudes: value at the tip, one hue, no legend. */
export function BarList({ data, height = 22, ordinal = false, valueFmt = tick }: { data: BarDatum[]; height?: number; ordinal?: boolean; valueFmt?: (n: number) => string }) {
  const { ref, width } = useMeasure<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const labelW = 168;
  const valueW = 56;
  const w = Math.max(320, width);
  const max = Math.max(1, ...data.map((d) => d.value));
  const iw = Math.max(40, w - labelW - valueW);
  const h = data.length * (height + 8);
  return (
    <div className="relative w-full overflow-hidden" ref={ref}>
      <svg width={w} height={h} role="img" aria-label="ranked values">
        {data.map((d, i) => {
          const bw = (d.value / max) * iw;
          const y = i * (height + 8);
          const fill = ordinal ? ORD[Math.min(ORD.length - 1, Math.round((i / Math.max(1, data.length - 1)) * (ORD.length - 1)))] : "var(--series-1)";
          return (
            <g key={d.label} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={0} y={y} width={w} height={height} fill="transparent" />
              <text x={0} y={y + height * 0.72} fontSize={11} fill="var(--text-secondary)">{fitText(d.label, labelW - 12)}<title>{d.label}</title></text>
              <path d={barPath(labelW, y + 2, Math.max(2, bw), height - 4, "right")} fill={fill} />
              <text x={labelW + Math.max(2, bw) + 6} y={y + height * 0.72} fontSize={11} fill="var(--text-primary)">{valueFmt(d.value)}</text>
            </g>
          );
        })}
      </svg>
      {hover !== null && data[hover]?.sub && (
        <Tooltip x={labelW} y={hover * (height + 8) + 40}>
          <div className="font-medium" style={{ color: "var(--text-primary)" }}>{data[hover].label}</div>
          <div style={{ color: "var(--text-secondary)" }}>{data[hover].sub}</div>
        </Tooltip>
      )}
    </div>
  );
}

export interface Cell { row: string; col: string; value: number; sub?: string }

/** Sequential heatmap: one hue light to dark, 2px surface gaps, labels only where they fit. */
export function Heatmap({ rows, cols, cells, valueFmt = money }: { rows: string[]; cols: string[]; cells: Cell[]; valueFmt?: (n: number) => string }) {
  const { ref, width } = useMeasure<HTMLDivElement>();
  const [hover, setHover] = useState<Cell | null>(null);
  const labelW = 150;
  const rowH = 30;
  const w = Math.max(320, width);
  const cw = Math.max(48, (w - labelW) / Math.max(1, cols.length));
  const max = Math.max(1, ...cells.map((c) => c.value));
  const step = (v: number) => (v <= 0 ? -1 : Math.min(SEQ.length - 1, Math.floor((v / max) * SEQ.length)));
  return (
    <div className="relative w-full overflow-hidden" ref={ref}>
      <svg width={w} height={rows.length * rowH + 22} role="img" aria-label="open exposure by root cause and age">
        {cols.map((c, j) => (
          <text key={c} x={labelW + j * cw + cw / 2} y={12} fontSize={10} textAnchor="middle" fill="var(--text-muted)">{c}</text>
        ))}
        {rows.map((r, i) => (
          <g key={r}>
            <text x={0} y={22 + i * rowH + rowH * 0.62} fontSize={11} fill="var(--text-secondary)">{fitText(r.replace(/_/g, " "), labelW - 12)}<title>{r.replace(/_/g, " ")}</title></text>
            {cols.map((c, j) => {
              const cell = cells.find((x) => x.row === r && x.col === c) ?? { row: r, col: c, value: 0 };
              const s = step(cell.value);
              const fits = cw > 62 && cell.value > 0;
              return (
                <g key={c} onMouseEnter={() => setHover(cell)} onMouseLeave={() => setHover(null)}>
                  <rect x={labelW + j * cw + 1} y={22 + i * rowH + 1} width={cw - 2} height={rowH - 2} rx={2}
                        fill={s < 0 ? "var(--surface-1)" : SEQ[s]} stroke={s < 0 ? "var(--grid)" : "none"} strokeWidth={1} />
                  {fits && (
                    <text x={labelW + j * cw + cw / 2} y={22 + i * rowH + rowH * 0.64} fontSize={10} textAnchor="middle"
                          fill={s >= 3 ? "#ffffff" : "var(--text-primary)"}>
                      {valueFmt(cell.value)}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        ))}
      </svg>
      {hover && (
        <Tooltip x={labelW} y={40}>
          <div className="font-medium" style={{ color: "var(--text-primary)" }}>{hover.row.replace("_", " ")} · {hover.col} days</div>
          <div style={{ color: "var(--text-secondary)" }}>{valueFmt(hover.value)}{hover.sub ? ` · ${hover.sub}` : ""}</div>
        </Tooltip>
      )}
    </div>
  );
}

export function StatTile({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "good" | "critical" }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="text-xs uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>{label}</div>
      <div className="mt-0.5 text-2xl font-semibold tabular-nums"
           style={{ color: tone === "good" ? "var(--status-good)" : tone === "critical" ? "var(--status-critical)" : "var(--text-primary)" }}>
        {value}
      </div>
      {hint && <div className="text-xs" style={{ color: "var(--text-secondary)" }}>{hint}</div>}
    </div>
  );
}
