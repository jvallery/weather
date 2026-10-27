// SPDX-License-Identifier: MIT
import { useEffect, useRef } from "react";
import type { Units } from "../lib/contracts";
import type { Aggregates, MetricDefinition } from "../lib/weather-api";
import { plotTime, metricLabel, metricValue } from "../lib/format";

export type HistoryRow = { start: string; unit: string; coverage: number; values: Record<string, number | null>; notes: Record<string, string | null> };
/** One row per interval; a statistic without support stays null (a gap), never zero. */
export function historyRows(aggregates: Aggregates, metric: string, statistics: string[]): HistoryRow[] {
  const rows = new Map<string, HistoryRow>();
  for (const item of aggregates.items) {
    if (item.metric !== metric || !statistics.includes(item.statistic)) continue;
    const row = rows.get(item.start) || { start: item.start, unit: item.unit, coverage: item.coverage.fraction, values: {}, notes: {} };
    row.values[item.statistic] = item.value;
    row.notes[item.statistic] = item.value === null ? item.reason : null;
    rows.set(item.start, row);
  }
  return [...rows.values()].sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
}
export function historySeries(rows: HistoryRow[], statistics: string[]): [number[], ...(number | null)[][]] {
  return [rows.map((row) => Date.parse(row.start) / 1000), ...statistics.map((statistic) => rows.map((row) => row.values[statistic] ?? null))];
}
export function HistoryPlot({ rows, metric, statistics, units, definition }: { rows: HistoryRow[]; metric: string; statistics: string[]; units: Units; definition?: MetricDefinition }) {
  const target = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!target.current || !rows.length) return;
    let disposed = false;
    let plot: { destroy(): void; setSize(size: { width: number; height: number }): void } | undefined;
    const element = target.current;
    const observer = new ResizeObserver(() => plot?.setSize({ width: element.clientWidth, height: 240 }));
    observer.observe(element);
    void import("uplot").then(({ default: UPlot }) => {
      if (disposed) return;
      // Canonical values stay authoritative; only tick presentation changes units.
      const unit = rows[0].unit;
      const label = metricLabel(metric, definition);
      plot = new UPlot({ width: element.clientWidth, height: 240, padding: [null, 64, null, 16], 
        series: [{}, ...statistics.map((statistic, index) => ({ label: `${label} ${statistic}`, stroke: index === 0 ? "#0e5a43" : "#9bb5ab", width: index === 0 ? 2 : 1, spanGaps: false }))],
        axes: [{ stroke: "#626b76", font: "12px IBM Plex Sans", space: 150, size: 64, values: (_plot, values) => values.map(plotTime) }, { stroke: "#626b76", values: (_plot, values) => values.map((value) => metricValue(value, unit, units, definition)) }],
        cursor: { drag: { x: true, y: false } }, legend: { show: false },
      }, historySeries(rows, statistics), element);
    });
    return () => { disposed = true; observer.disconnect(); plot?.destroy(); };
  }, [definition, rows, metric, statistics, units]);
  return <div ref={target} className="plot" role="img" aria-label={`${metricLabel(metric, definition)} plot. Intervals without observations are gaps. The complete data table follows.`} />;
}
