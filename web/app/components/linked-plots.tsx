// SPDX-License-Identifier: MIT
import { useEffect, useRef, type KeyboardEvent } from "react";
import type { Units } from "../lib/contracts";
import { localTime, metricLabel, metricValue, plotTime } from "../lib/format";
import { epochSeconds, fromEpochSeconds, instant, iso, type Instant, type Span } from "../lib/time-map";
import type { Aggregates, MetricDefinition, MetricRegistry } from "../lib/weather-api";

/** Measured weather on the video's own source interval: one aggregate request per statistic. */
export const PLOT_REQUESTS = [
  { metrics: ["temperature", "humidity", "station_pressure", "wind_avg"], statistics: ["mean"] },
  { metrics: ["wind_gust"], statistics: ["max"] },
  { metrics: ["precipitation"], statistics: ["sum"] },
] as const;
export const PLOT_PANELS = [
  { id: "temperature", series: [["temperature", "mean"]] },
  { id: "humidity", series: [["humidity", "mean"]] },
  { id: "pressure", series: [["station_pressure", "mean"]] },
  { id: "wind", series: [["wind_avg", "mean"], ["wind_gust", "max"]] },
  { id: "rain", series: [["precipitation", "sum"]] },
] as const;
const RESOLUTIONS = [60, 120, 300, 600, 900, 1800, 3600];
const MAX_BINS = 300;
/** The finest epoch-aligned resolution that keeps a segment to at most 300 bins per series. */
export function plotResolution(span: Span): number {
  const seconds = epochSeconds(span.end - span.start);
  return RESOLUTIONS.find((resolution) => Math.ceil(seconds / resolution) + 1 <= MAX_BINS) || 3600;
}
/** Exactly the segment's [a, b): plot bounds are never the calendar day. */
export function plotQueries(span: Span): URLSearchParams[] {
  const resolution = String(plotResolution(span));
  return PLOT_REQUESTS.map((request) => {
    const query = new URLSearchParams({ start: iso(span.start), end: iso(span.end), resolution });
    for (const metric of request.metrics) query.append("metrics", metric);
    for (const statistic of request.statistics) query.append("statistics", statistic);
    return query;
  });
}
export type PlotBin = { start: Instant; end: Instant; values: Record<string, number | null>; coverage: Record<string, number>; units: Record<string, string> };
const key = (metric: string, statistic: string) => `${metric}:${statistic}`;
/** One row per aggregate interval inside [a, b); an interval without support stays null, never zero. */
export function plotBins(span: Span, responses: readonly Aggregates[]): PlotBin[] {
  const bins = new Map<bigint, PlotBin>();
  for (const response of responses) for (const item of response.items) {
    const start = instant(item.start), end = instant(item.end);
    if (start === null || end === null || start < span.start || end > span.end || start >= end) continue;
    const bin = bins.get(start) || { start, end, values: {}, coverage: {}, units: {} };
    const name = key(item.metric, item.statistic);
    bin.values[name] = item.value; bin.coverage[name] = item.coverage.fraction; bin.units[name] = item.unit;
    bins.set(start, bin);
  }
  return [...bins.values()].sort((a, b) => (a.start < b.start ? -1 : 1));
}
export function binAt(bins: readonly PlotBin[], at: Instant | null): number | null {
  if (at === null) return null;
  const index = bins.findIndex((bin) => bin.start <= at && at < bin.end);
  return index < 0 ? null : index;
}
const STAT_LABELS: Record<string, string> = { mean: "mean", max: "maximum", sum: "total" };
export function seriesLabel(metric: string, statistic: string, registry: MetricRegistry | null): string {
  // A per-report rain amount summed over an interval is that interval's rain total.
  if (metric === "precipitation" && statistic === "sum") return "Rain total";
  return `${metricLabel(metric, definitionOf(metric, registry))} ${STAT_LABELS[statistic] || statistic}`;
}
const definitionOf = (metric: string, registry: MetricRegistry | null): MetricDefinition | undefined => registry?.metrics.find((item) => item.metric === metric);
export function binValue(bin: PlotBin, metric: string, statistic: string, units: Units, registry: MetricRegistry | null): string {
  const name = key(metric, statistic), value = bin.values[name];
  if (value === undefined || value === null) return "no observations";
  return metricValue(value, bin.units[name], units, definitionOf(metric, registry));
}
/** A per-series text summary: range of plotted values, intervals with data and gaps. */
export function seriesSummary(bins: readonly PlotBin[], metric: string, statistic: string, units: Units, registry: MetricRegistry | null): string {
  const name = key(metric, statistic);
  const valued = bins.filter((bin) => bin.values[name] !== null && bin.values[name] !== undefined);
  const label = seriesLabel(metric, statistic, registry);
  if (!valued.length) return `${label}: no observations in this recording's interval.`;
  const values = valued.map((bin) => bin.values[name]!);
  const low = valued[values.indexOf(Math.min(...values))], high = valued[values.indexOf(Math.max(...values))];
  const supported = bins.reduce((sum, bin) => sum + (bin.coverage[name] || 0) * epochSeconds(bin.end - bin.start), 0);
  const total = bins.reduce((sum, bin) => sum + epochSeconds(bin.end - bin.start), 0);
  const gaps = bins.length - valued.length;
  return `${label}: ${binValue(low, metric, statistic, units, registry)} (${localTime(iso(low.start))}) to ${binValue(high, metric, statistic, units, registry)} (${localTime(iso(high.start))}); ${valued.length} of ${bins.length} intervals have values${gaps ? `, ${gaps} without observations` : ""}; coverage ${(total ? (supported / total) * 100 : 0).toFixed(1)}%.`;
}

const STROKES = ["#0e5a43", "#7a4f9a"];
type Plot = { destroy(): void; setSize(size: { width: number; height: number }): void; valToPos(value: number, scale: string): number; posToVal(position: number, scale: string): number; over: HTMLElement; bbox: { width: number } };
const HEIGHT = 150;

/**
 * Linked uPlot panels. The x scale is exactly [a, b); capture gaps (slate frames) are shaded at
 * their true length; telemetry gaps are breaks, never interpolated. Selecting an instant (pointer)
 * or a bin (keyboard) calls onSelect; the shared UTC cursor is drawn without rebuilding plots.
 */
export function LinkedPlots({ span, bins, gaps, cursor, onSelect, units, registry, describedBy }: { span: Span; bins: readonly PlotBin[]; gaps: readonly Span[]; cursor: Instant | null; onSelect: (at: Instant) => void; units: Units; registry: MetricRegistry | null; describedBy: string }) {
  const targets = useRef<(HTMLDivElement | null)[]>([]);
  const plots = useRef<{ plot: Plot; line: HTMLDivElement }[]>([]);
  const select = useRef(onSelect);
  useEffect(() => { select.current = onSelect; }, [onSelect]);
  const cursorRef = useRef(cursor);
  const place = () => {
    for (const { plot, line } of plots.current) {
      const at = cursorRef.current;
      line.hidden = at === null;
      if (at !== null) line.style.transform = `translateX(${Math.round(plot.valToPos(epochSeconds(at), "x"))}px)`;
    }
  };
  useEffect(() => { cursorRef.current = cursor; place(); }, [cursor]);
  useEffect(() => {
    let disposed = false;
    const observers: ResizeObserver[] = [];
    void import("uplot").then(({ default: UPlot }) => {
      if (disposed) return;
      const xs = bins.map((bin) => epochSeconds(bin.start + (bin.end - bin.start) / 2n));
      PLOT_PANELS.forEach((panel, index) => {
        const element = targets.current[index];
        if (!element) return;
        const definition = definitionOf(panel.series[0][0], registry);
        const first = key(panel.series[0][0], panel.series[0][1]);
        const unit = bins.find((bin) => bin.units[first])?.units[first] || definition?.canonical_unit || "";
        const shade = (plot: { ctx: CanvasRenderingContext2D; bbox: { top: number; height: number }; valToPos(value: number, scale: string, canvas?: boolean): number }) => {
          plot.ctx.save(); plot.ctx.fillStyle = "rgba(179, 38, 30, 0.06)";
          for (const gap of gaps) {
            const left = plot.valToPos(epochSeconds(gap.start), "x", true), right = plot.valToPos(epochSeconds(gap.end), "x", true);
            plot.ctx.fillRect(left, plot.bbox.top, Math.max(1, right - left), plot.bbox.height);
          }
          plot.ctx.restore();
        };
        const plot = new UPlot({
          width: element.clientWidth, height: HEIGHT, padding: [8, 16, 0, 8],
          scales: { x: { time: true, range: () => [epochSeconds(span.start), epochSeconds(span.end)] },
            // Rain starts at zero and a dry interval keeps a small scale (2.5 mm), not an inflated one.
            ...(panel.id === "rain" ? { y: { range: (_plot: unknown, _min: number, max: number): [number, number] => [0, Math.max(2.5, (max || 0) * 1.1)] } } : {}) },
          series: [{}, ...panel.series.map(([metric, statistic], position) => ({ label: seriesLabel(metric, statistic, registry), stroke: STROKES[position], width: position ? 1 : 2, spanGaps: false, points: { show: true, size: 3 } }))],
          axes: [{ stroke: "#626b76", font: "12px IBM Plex Sans", space: 140, size: 48, values: (_plot, values) => values.map(plotTime) }, { stroke: "#626b76", size: 72, values: (_plot, values) => values.map((value) => metricValue(value, unit, units, definition)) }],
          cursor: { drag: { x: false, y: false }, sync: { key: "linked-weather" } }, legend: { show: false },
          hooks: { drawClear: [shade] },
        }, [xs, ...panel.series.map(([metric, statistic]) => bins.map((bin) => bin.values[key(metric, statistic)] ?? null))], element) as unknown as Plot;
        const line = document.createElement("div");
        line.className = "plot-cursor"; line.hidden = true;
        plot.over.appendChild(line);
        plot.over.addEventListener("click", (event) => select.current(fromEpochSeconds(plot.posToVal(event.clientX - plot.over.getBoundingClientRect().left, "x"))));
        plots.current.push({ plot, line });
        const observer = new ResizeObserver(() => { plot.setSize({ width: element.clientWidth, height: HEIGHT }); place(); });
        observer.observe(element); observers.push(observer);
      });
      place();
    });
    return () => { disposed = true; for (const observer of observers) observer.disconnect(); for (const { plot } of plots.current) plot.destroy(); plots.current = []; };
  }, [bins, gaps, registry, span, units]);
  const current = binAt(bins, cursor);
  const step = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!bins.length) return;
    const from = current ?? -1;
    const next = { ArrowRight: from + 1, ArrowUp: from + 1, ArrowLeft: from - 1, ArrowDown: from - 1, PageUp: from + 12, PageDown: from - 12, Home: 0, End: bins.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    const bin = bins[Math.min(bins.length - 1, Math.max(0, next))];
    onSelect(bin.start + (bin.end - bin.start) / 2n);
  };
  const half = bins.length ? Math.round(epochSeconds(bins[0].end - bins[0].start) / 2) : 0;
  const valueText = current === null ? "No interval selected" : `Interval starting ${localTime(iso(bins[current].start))}, ±${half} s; ${PLOT_PANELS.flatMap((panel) => panel.series.map(([metric, statistic]) => `${seriesLabel(metric, statistic, registry)} ${binValue(bins[current], metric, statistic, units, registry)}`)).join("; ")}`;
  return <div className="linked-plots" role="slider" tabIndex={0} aria-label="Measured weather plots · arrow keys step one interval and seek the video" aria-describedby={describedBy}
    aria-valuemin={0} aria-valuemax={Math.max(0, bins.length - 1)} aria-valuenow={current ?? 0} aria-valuetext={valueText} onKeyDown={step}>
    {PLOT_PANELS.map((panel, index) => <figure key={panel.id} className="linked-plot"><figcaption>{panel.series.map(([metric, statistic]) => seriesLabel(metric, statistic, registry)).join(" and ")}</figcaption><div ref={(element) => { targets.current[index] = element; }} className="plot plot-small" aria-hidden="true" /></figure>)}
  </div>;
}
