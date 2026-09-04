import { useLayoutEffect, useRef, useState } from "react";

/** Measures the container so strokes stay exactly 2px instead of being scaled by a viewBox. */
export function useMeasure<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    ro.observe(el);
    setWidth(Math.floor(el.getBoundingClientRect().width));
    return () => ro.disconnect();
  }, []);
  return { ref, width };
}

/** Categorical slots 1-3 of the validated palette, in fixed order (never cycled). */
export const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];
export const SEQ = ["var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)", "var(--seq-5)"];
/** Ordinal ramp: no step lighter than 250 on the light surface. */
export const ORD = ["#86b6ef", "#6da7ec", "#3987e5", "#2a78d6", "#256abf", "#1c5cab"];

export const money = (n: number) =>
  n >= 1000 ? `$${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : `$${n.toFixed(0)}`;
export const tick = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(0)}k` : String(Math.round(n)));

/** Bar with a 4px rounded data-end and a square baseline, per the mark spec. */
export function barPath(x: number, y: number, w: number, h: number, dir: "up" | "right"): string {
  const r = Math.min(4, dir === "up" ? h : w, dir === "up" ? w / 2 : h / 2);
  if (r <= 0) return "";
  return dir === "up"
    ? `M${x},${y + h} L${x},${y + r} Q${x},${y} ${x + r},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h} Z`
    : `M${x},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} L${x},${y + h} Z`;
}

const FONT_STACK =
  'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
let measureCtx: CanvasRenderingContext2D | null | undefined;
const fitCache = new Map<string, string>();

/**
 * Truncates a label to a pixel budget, adding an ellipsis, so SVG text can never
 * overrun into the plot area. Measures with a canvas context; if that is not
 * available (SSR, jsdom) it falls back to a conservative per-character estimate.
 */
export function fitText(text: string, maxPx: number, fontPx = 11): string {
  const key = `${fontPx}|${maxPx}|${text}`;
  const cached = fitCache.get(key);
  if (cached !== undefined) return cached;
  if (measureCtx === undefined) {
    measureCtx = typeof document === "undefined" ? null : document.createElement("canvas").getContext("2d");
  }
  const width = (s: string) => {
    if (!measureCtx) return s.length * fontPx * 0.62;
    measureCtx.font = `${fontPx}px ${FONT_STACK}`;
    return measureCtx.measureText(s).width;
  };
  let out = text;
  if (width(out) > maxPx) {
    let lo = 0;
    let hi = text.length;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) / 2);
      if (width(`${text.slice(0, mid).trimEnd()}…`) <= maxPx) lo = mid;
      else hi = mid - 1;
    }
    out = lo <= 0 ? "…" : `${text.slice(0, lo).trimEnd()}…`;
  }
  fitCache.set(key, out);
  return out;
}
