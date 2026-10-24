// SPDX-License-Identifier: MIT
import { useEffect, useRef } from "react";
import type { History, MetricDefinition, Units } from "../lib/contracts";
import { plotTime, metricLabel, metricValue } from "../lib/format";

export function historySeries(history: History, metric: string): [number[], (number | null)[]] {
  const points = new Map<number, number | null>();
  for (const point of history.points.filter((point) => point.metric === metric)) {
    const time = Date.parse(point.observed_at);
    const inGap = history.gaps.some((gap) => time >= Date.parse(gap.start) && time < Date.parse(gap.end));
    points.set(time, point.quality === "valid" && !inGap ? point.value : null);
  }
  for (const gap of history.gaps) { points.set(Date.parse(gap.start), null); points.set(Date.parse(gap.end), null); }
  const times = [...points.keys()].sort((a, b) => a - b);
  return [times.map((time) => time / 1000), times.map((time) => points.get(time) ?? null)];
}
export function HistoryPlot({ history, metric, units, definition }: { history: History; metric: string; units: Units; definition?: MetricDefinition }) {
  const target = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!target.current || !history.points.length) return;
    let disposed = false;
    let plot: { destroy(): void; setSize(size: { width: number; height: number }): void } | undefined;
    const element = target.current;
    const observer = new ResizeObserver(() => plot?.setSize({ width: element.clientWidth, height: 240 }));
    observer.observe(element);
    void import("uplot").then(({ default: UPlot }) => {
      if (disposed) return;
      const data = historySeries(history, metric);
      // Canonical values stay authoritative; only tick presentation changes units.
      const unit = history.points.find((point) => point.metric === metric)?.unit || "";
      plot = new UPlot({ width: element.clientWidth, height: 240, title: `${metricLabel(metric, definition)} · explicit gaps`,
        series: [{}, { label: metricLabel(metric, definition), stroke: "#0e5a43", width: 2, spanGaps: false }],
        axes: [{ stroke: "#626b76", font: "12px IBM Plex Sans", space: 150, size: 64, values: (_plot, values) => values.map(plotTime) }, { stroke: "#626b76", values: (_plot, values) => values.map((value) => metricValue(value, unit, units, definition)) }],
        cursor: { drag: { x: true, y: false } }, legend: { show: false },
      }, data, element);
    });
    return () => { disposed = true; observer.disconnect(); plot?.destroy(); };
  }, [definition, history, metric, units]);
  return <div ref={target} className="plot" role="img" aria-label={`${metricLabel(metric, definition)} plot. Missing intervals are gaps. The complete data table follows.`} />;
}
