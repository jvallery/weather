// SPDX-License-Identifier: MIT
import { Form, useLoaderData, type LoaderFunctionArgs } from "react-router";
import plotStyles from "uplot/dist/uPlot.min.css?url";
import { HistoryPlot, historyRows } from "../components/history-plot";
import { UnitForm } from "../components/common";
import { conversionAvailable, localTime, metricLabel, metricValue } from "../lib/format";
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
  return spanMs <= 2 * DAY ? "300" : spanMs <= 7 * DAY ? "3600" : "10800";
}
export async function loader({ request }: LoaderFunctionArgs) {
  const url = new URL(request.url);
  const now = Date.now();
  const end = url.searchParams.get("end") || new Date(now).toISOString();
  const start = url.searchParams.get("start") || new Date(now - DAY).toISOString();
  const metric = url.searchParams.get("metric") || "temperature";
  if (!/^[a-z][a-z0-9_]{0,63}$/.test(metric) || !intervalSchema.safeParse({ start, end }).success) throw new Response("Invalid history range", { status: 400 });
  const span = Date.parse(end) - Date.parse(start);
  if (span > 31 * DAY) throw new Response("Select a history interval of 31 days or less; older data remains available in successive intervals", { status: 400 });
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
  return { aggregates: result.kind === "ok" ? result.data : null, issue: result.kind === "invalid" ? result.issue : null, registry, start, end, metric, statistics, resolution, units: unitsFrom(url), exportUrl: `/api/v1/weather/observations?${raw}`, csvUrl: `/api/v1/weather/history.csv?${parameters}` };
}
export default function HistoryPage() {
  const { aggregates, issue, registry, start, end, metric, statistics, resolution, units, exportUrl, csvUrl } = useLoaderData<typeof loader>();
  const definition = registry?.metrics.find((row) => row.metric === metric);
  const rows = aggregates ? historyRows(aggregates, metric, statistics) : [];
  const hasValues = rows.some((row) => row.values[statistics[0]] !== null);
  const step = resolution === "day" ? "local day" : `${Number(resolution) / 60} min`;
  return <><h1>Measured weather history</h1><p>Each point summarizes one {step} interval of source observations (statistic: {statistics.join(", ")}). Intervals without observations are gaps, never zero; coverage is the share of each interval with valid data. Local days have 23, 24 or 25 hours; repeated-hour labels include their UTC offset.</p>
    <Form key={`${start}|${end}|${metric}`} method="get" className="filters"><label>Source start (UTC ISO)<input name="start" defaultValue={start} required /></label><label>Source end (UTC ISO, exclusive)<input name="end" defaultValue={end} required /></label><label>Metric{registry ? <select name="metric" defaultValue={metric}>{registry.metrics.filter((item) => chartStatistics(item).length > 0).map((item) => <option value={item.metric} key={item.metric}>{item.label} · {item.canonical_unit}</option>)}</select> : <input name="metric" defaultValue={metric} required pattern="[a-z][a-z0-9_]{0,63}" />}</label><input type="hidden" name="units" value={units} /><button type="submit">Show history</button></Form>
    <UnitForm units={units}><input type="hidden" name="start" value={start} /><input type="hidden" name="end" value={end} /><input type="hidden" name="metric" value={metric} /></UnitForm>
    {!conversionAvailable(definition?.canonical_unit || "", units, definition) && <p className="notice">Display conversions unavailable; source units shown.</p>}
    {issue ? <p className="fault">The API rejected this history selection. Select a shorter interval or another metric.{issue.suggestedResolutionSeconds !== null && ` The suggested minimum resolution is ${issue.suggestedResolutionSeconds} seconds.`}</p> : !aggregates ? <p className="fault">History API unavailable. No substitute history or zero-filled series is displayed.</p> : rows.length === 0 ? <p>No intervals were returned for this source window.</p> : <>
      <h2>{metricLabel(metric, definition)}</h2><p>{localTime(aggregates.start)} → {localTime(aggregates.end)}</p>
      {hasValues ? <HistoryPlot rows={rows} metric={metric} statistics={statistics} units={units} definition={definition} /> : <p>No valid measured values are available to plot.</p>}
      <p className="small">Interactive plotting uses JavaScript. The complete table below is available without it.</p>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Weather history table"><table><caption>Summarized intervals</caption><thead><tr><th scope="col">Interval start · America/Denver</th>{statistics.map((statistic) => <th scope="col" key={statistic}>{statistic === "vector_mean" ? "direction" : statistic}</th>)}<th scope="col">Coverage</th></tr></thead><tbody>{rows.map((row) => <tr key={row.start}><th scope="row"><time dateTime={row.start}>{localTime(row.start)}</time></th>{statistics.map((statistic) => <td key={statistic}>{row.values[statistic] === null && row.notes[statistic] ? row.notes[statistic] : metricValue(row.values[statistic], row.unit, units, definition)}</td>)}<td>{(row.coverage * 100).toFixed(0)}%</td></tr>)}</tbody></table></div>
    </>}
    {aggregates ? <p><a className="button-link" href={csvUrl} download>Download this history as CSV</a> <a className="button-link" href={exportUrl} download>Export raw observations for this window as JSON</a></p> : <p>Export is unavailable until observations can be read.</p>}<p className="small">The CSV holds the charted intervals: one row per interval, metric and statistic, with units, coverage and an empty value for a gap (#166). JSON exports carry original source and receipt times, units and quality. Follow next_cursor for later pages; query limits do not delete older history.</p>
  </>;
}
