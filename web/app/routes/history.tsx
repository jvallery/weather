// SPDX-License-Identifier: MIT
import { Form, useLoaderData, type LoaderFunctionArgs } from "react-router";
import { Link } from "../components/unit-link";
import plotStyles from "uplot/dist/uPlot.min.css?url";
import { HistoryPlot, historyRows, type HistoryRow } from "../components/history-plot";
import { PageHead } from "../components/common";
import { conversionAvailable, dateShift, humanize, localDate, localDayStart, localTime, metricLabel, metricValue, shortDateTime } from "../lib/format";
import { intervalSchema } from "../lib/contracts";
import { aggregatesRead, metricRegistryRead } from "../lib/public-api.server";
import { unitsFrom } from "../lib/route-params";
import type { MetricDefinition } from "../lib/weather-api";
export const meta = () => [{ title: "Measured weather history · Weather" }];
export const links = () => [{ rel: "stylesheet", href: plotStyles }];
const DAY = 86_400_000;
/** The statistics charted for a metric: mean with its range, or the metric's own summary. */
export function chartStatistics(definition?: MetricDefinition): string[] {
  const statistics = definition?.statistics || ["observed", "mean", "min", "max"];
  if (statistics.includes("mean")) return ["mean", "min", "max"];
  const single = ["sum", "max", "min", "vector_mean"].find((statistic) => statistics.includes(statistic));
  return single ? [single] : [];
}
/** One aggregate request covers the whole window, so a 24 h view is never truncated. */
export function resolutionFor(spanMs: number): string {
  return spanMs <= 2 * DAY ? "300" : spanMs <= 7 * DAY ? "3600" : spanMs <= 31 * DAY ? "10800" : "day";
}
export async function loader({ request }: LoaderFunctionArgs) {
  const url = new URL(request.url);
  const now = Date.now();
  // A local date range (the date selector: first and last day, inclusive) or explicit UTC bounds.
  const from = url.searchParams.get("from"), to = url.searchParams.get("to");
  if ((from || to) && !(from && to && DATE.test(from) && DATE.test(to) && from <= to)) throw new Response("Invalid history dates", { status: 400 });
  const end = from && to ? localDayStart(dateShift(to, 1)) : url.searchParams.get("end") || new Date(now).toISOString();
  const start = from && to ? localDayStart(from) : url.searchParams.get("start") || new Date(now - DAY).toISOString();
  const metric = url.searchParams.get("metric") || "temperature";
  if (!/^[a-z][a-z0-9_]{0,63}$/.test(metric) || !intervalSchema.safeParse({ start, end }).success) throw new Response("Invalid history range", { status: 400 });
  const span = Date.parse(end) - Date.parse(start);
  if (span > 366 * DAY) throw new Response("Select a history interval of a year (366 days) or less; older data remains available in successive intervals", { status: 400 });
  const registry = await metricRegistryRead(request);
  const definition = registry?.metrics.find((item) => item.metric === metric);
  if (registry && !definition) throw new Response("Unknown public metric", { status: 400 });
  const statistics = chartStatistics(definition);
  if (!statistics.length) throw new Response("This metric has no history; select a measured metric", { status: 400 });
  const resolution = resolutionFor(span);
  const parameters = new URLSearchParams({ start, end, resolution, metrics: metric });
  for (const statistic of statistics) parameters.append("statistics", statistic);
  const result = await aggregatesRead(request, parameters);
  const raw = new URLSearchParams({ start, end, metrics: metric, limit: "5000" });
  // The raw export reads at most 31 days per request; longer views export the charted intervals.
  return { aggregates: result.kind === "ok" ? result.data : null, issue: result.kind === "invalid" ? result.issue : null, registry, start, end, metric, statistics, resolution, units: unitsFrom(url), now, exportUrl: span <= 31 * DAY ? `/api/v1/weather/observations?${raw}` : null, from: from && to ? from : localDate(start), to: from && to ? to : localDate(new Date(Date.parse(end) - 1).toISOString()) };
}
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const PRESETS = [["24 h", DAY], ["7 days", 7 * DAY], ["31 days", 31 * DAY], ["90 days", 90 * DAY], ["365 days", 365 * DAY]] as const;
/** Low, mean, high (or total) over the window, from the interval statistics; gaps are skipped, never zero. */
export function summarize(rows: HistoryRow[], statistics: string[]): { label: string; value: number; at?: string }[] {
  const series = (statistic: string) => rows.flatMap((row) => row.values[statistic] === null || row.values[statistic] === undefined ? [] : [{ value: row.values[statistic]!, at: row.start }]);
  if (statistics[0] === "mean") {
    const means = series("mean"), lows = series("min"), highs = series("max");
    if (!means.length) return [];
    const low = (lows.length ? lows : means).reduce((a, b) => (b.value < a.value ? b : a));
    const high = (highs.length ? highs : means).reduce((a, b) => (b.value > a.value ? b : a));
    return [{ label: "Low", value: low.value, at: low.at }, { label: "Mean", value: means.reduce((sum, item) => sum + item.value, 0) / means.length }, { label: "High", value: high.value, at: high.at }];
  }
  const values = series(statistics[0]);
  if (!values.length) return [];
  if (statistics[0] === "sum") return [{ label: "Total", value: values.reduce((sum, item) => sum + item.value, 0) }];
  if (statistics[0] === "max") { const high = values.reduce((a, b) => (b.value > a.value ? b : a)); return [{ label: "Highest", value: high.value, at: high.at }]; }
  if (statistics[0] === "min") { const low = values.reduce((a, b) => (b.value < a.value ? b : a)); return [{ label: "Lowest", value: low.value, at: low.at }]; }
  return [];
}
export default function HistoryPage() {
  const { aggregates, issue, registry, start, end, metric, statistics, resolution, units, exportUrl, now, from, to } = useLoaderData<typeof loader>();
  const definition = registry?.metrics.find((row) => row.metric === metric);
  const rows = aggregates ? historyRows(aggregates, metric, statistics) : [];
  const hasValues = rows.some((row) => row.values[statistics[0]] !== null);
  const step = resolution === "day" ? "local day" : `${Number(resolution) / 60}-minute`;
  const span = Date.parse(end) - Date.parse(start);
  const live = Math.abs(now - Date.parse(end)) < 15 * 60_000;
  const query = (changes: Record<string, string>) => `?${new URLSearchParams({ start, end, metric, ...(units === "metric" ? { units } : {}), ...changes })}`;
  const preset = (ms: number) => query({ start: new Date(now - ms).toISOString(), end: new Date(now).toISOString() });
  const summary = summarize(rows, statistics);
  const coverage = rows.length ? rows.reduce((sum, row) => sum + row.coverage, 0) / rows.length : null;
  const metrics = registry ? registry.metrics.filter((item) => chartStatistics(item).length > 0) : [];
  return <>
    <PageHead eyebrow="Station history · America/Denver" title={metricLabel(metric, definition)} lede={<><time dateTime={start}>{shortDateTime(start)}</time> → <time dateTime={end}>{shortDateTime(end)}</time> · one point per {step} interval; gaps stay empty, never zero.</>} />
    <div className="actions" role="group" aria-label="History selection">
      <ul className="chips" aria-label="Range">{PRESETS.map(([label, ms]) => <li key={label}><Link to={preset(ms)} aria-current={live && Math.abs(span - ms) < 60_000 ? "page" : undefined}>{label}</Link></li>)}</ul>
    </div>
    <Form key={`${from}|${to}|${metric}`} method="get" className="filters date-range" aria-label="Date range"><label>From<input type="date" name="from" defaultValue={from} max={to} required /></label><label>To<input type="date" name="to" defaultValue={to} required /></label><input type="hidden" name="metric" value={metric} />{units === "metric" && <input type="hidden" name="units" value="metric" />}<button type="submit">Show</button><span className="small">Local days, America/Denver; up to a year. Longer than 31 days shows one point per day.</span></Form>
    {metrics.length > 0 && <ul className="chips chips-sm" aria-label="Measurement">{metrics.map((item) => <li key={item.metric}><Link to={query({ metric: item.metric })} aria-current={item.metric === metric ? "page" : undefined}>{item.label}</Link></li>)}</ul>}
    {!conversionAvailable(definition?.canonical_unit || "", units, definition) && <p className="notice">Display conversions unavailable; source units shown.</p>}
    {aggregates?.imported && <p className="notice" data-provenance="imported">Imported history: through <time dateTime={aggregates.imported.last}>{shortDateTime(aggregates.imported.last)}</time>, values come from this station&apos;s readings kept by the greenhouse controller (verdify-platform), about one every 1–5 minutes, with rain only where the station reported it. The live receiver covers everything after.</p>}
    <section className="section" aria-labelledby="history-title"><h2 id="history-title" className="screen-reader">{metricLabel(metric, definition)} over the selected window</h2>
    {issue ? <p className="fault">The API rejected this history selection. Select a shorter interval or another metric.{issue.suggestedResolutionSeconds !== null && ` The suggested minimum resolution is ${issue.suggestedResolutionSeconds} seconds.`}</p> : !aggregates ? <p className="fault">History API unavailable. No substitute history or zero-filled series is displayed.</p> : rows.length === 0 ? <p>No intervals were returned for this source window.</p> : <>
      {summary.length > 0 && <dl className="summary-stats">{summary.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{metricValue(item.value, rows[0].unit, units, definition)}{item.at && <small><time dateTime={item.at}>{shortDateTime(item.at)}</time></small>}</dd></div>)}{coverage !== null && <div><dt>Coverage</dt><dd>{(coverage * 100).toFixed(0)}%<small>{rows.length} intervals</small></dd></div>}</dl>}
      <div className="card card-tight">{hasValues ? <HistoryPlot rows={rows} metric={metric} statistics={statistics} units={units} definition={definition} /> : <p>No valid measured values are available to plot.</p>}</div>
      <details><summary>Data table · {rows.length} intervals</summary>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Weather history table"><table><caption>Summarized intervals ({statistics.join(", ")}); coverage is the share of each interval with valid data</caption><thead><tr><th scope="col">Interval start · America/Denver</th>{statistics.map((statistic) => <th scope="col" key={statistic}>{statistic === "vector_mean" ? "direction" : statistic}</th>)}<th scope="col">Coverage</th></tr></thead><tbody>{rows.map((row) => <tr key={row.start}><th scope="row"><time dateTime={row.start}>{localTime(row.start)}</time></th>{statistics.map((statistic) => <td key={statistic}>{row.values[statistic] === null && row.notes[statistic] ? humanize(row.notes[statistic]) : metricValue(row.values[statistic], row.unit, units, definition)}</td>)}<td>{(row.coverage * 100).toFixed(0)}%</td></tr>)}</tbody></table></div>
      </details>
    </>}
    </section>
    {!aggregates ? <p className="muted">Export is unavailable until observations can be read.</p> : exportUrl ? <p className="actions"><a className="button-link" href={exportUrl} download>Export raw observations for this window as JSON</a></p> : <p className="small">Raw observations export up to 31 days at a time; choose a shorter range to export.</p>}
    <p className="small">JSON exports carry original source and receipt times, units and quality; follow next_cursor for later pages.</p>
  </>;
}
