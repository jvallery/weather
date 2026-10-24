// SPDX-License-Identifier: MIT
import { Form, Link, useLoaderData, type LoaderFunctionArgs } from "react-router";
import plotStyles from "uplot/dist/uPlot.min.css?url";
import { HistoryPlot } from "../components/history-plot";
import { UnitForm } from "../components/common";
import { localTime, metricLabel, metricValue } from "../lib/format";
import { intervalSchema } from "../lib/contracts";
import { historyRead } from "../lib/public-api.server";
import { unitsFrom } from "../lib/route-params";
export const meta = () => [{ title: "Measured weather history · Weather" }];
export const links = () => [{ rel: "stylesheet", href: plotStyles }];
export async function loader({ request }: LoaderFunctionArgs) {
  const url = new URL(request.url);
  const now = Date.now();
  const end = url.searchParams.get("end") || new Date(now).toISOString();
  const start = url.searchParams.get("start") || new Date(now - 86_400_000).toISOString();
  const metric = url.searchParams.get("metric") || "temperature";
  if (!/^[a-z][a-z0-9_]{0,63}$/.test(metric) || !intervalSchema.safeParse({ start, end }).success) throw new Response("Invalid history range", { status: 400 });
  if (Date.parse(end) - Date.parse(start) > 31 * 86_400_000) throw new Response("Select a history interval of 31 days or less; older data remains available in successive intervals", { status: 400 });
  const parameters = new URLSearchParams({ start, end, metrics: metric, limit: "1000" });
  const cursor = url.searchParams.get("cursor");
  if (cursor && cursor.length > 2048) throw new Response("Invalid history cursor", { status: 400 });
  if (cursor) parameters.set("cursor", cursor);
  const history = await historyRead(request, parameters);
  return { history, start, end, metric, units: unitsFrom(url), exportUrl: `/api/v1/weather/observations?${parameters}` };
}
export default function HistoryPage() {
  const { history, start, end, metric, units, exportUrl } = useLoaderData<typeof loader>();
  const next = new URLSearchParams({ start, end, metric, units });
  if (history?.next_cursor) next.set("cursor", history.next_cursor);
  const hasValues = history?.points.some((point) => point.value !== null && point.quality === "valid");
  return <><h1>Measured weather history</h1><p>Calendar-day and source-UTC observations. A day may contain 23, 24 or 25 local hours; repeated-hour labels include their UTC offset.</p>
    <Form method="get" className="filters"><label>Source start (UTC ISO)<input name="start" defaultValue={start} required /></label><label>Source end (UTC ISO, exclusive)<input name="end" defaultValue={end} required /></label><label>Metric<input name="metric" defaultValue={metric} required pattern="[a-z][a-z0-9_]{0,63}" /></label><input type="hidden" name="units" value={units} /><button type="submit">Show observations</button></Form>
    <UnitForm units={units}><input type="hidden" name="start" value={start} /><input type="hidden" name="end" value={end} /><input type="hidden" name="metric" value={metric} /></UnitForm>
    {!history ? <p className="fault">History API unavailable. No substitute history or zero-filled series is displayed.</p> : history.points.length === 0 ? <p>No received measurements in this interval. History starts when direct station reception is commissioned.</p> : <>
      <h2>{metricLabel(metric)}</h2><p>Resolution: {history.resolution_seconds} s · statistic: {history.aggregation} · {localTime(history.interval.start)} → {localTime(history.interval.end)}</p>
      <h3>Coverage and gaps</h3><p>{history.gaps.length} reported missing interval{history.gaps.length === 1 ? "" : "s"}. Missing values remain gaps, never zero. Observation support intervals are listed in the table; a point without support does not imply complete coverage.</p>
      {history.gaps.length > 0 && <ul>{history.gaps.map((gap) => <li key={gap.start}>{localTime(gap.start)} → {localTime(gap.end)}</li>)}</ul>}
      {hasValues ? <HistoryPlot history={history} metric={metric} units={units} /> : <p>No valid measured values are available to plot.</p>}
      <p className="small">Interactive plotting uses JavaScript. The complete table below is available without it.</p>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Weather observations table"><table><caption>Received observations · this bounded page</caption><thead><tr><th scope="col">Source time · America/Denver</th><th scope="col">Value</th><th scope="col">Quality / reason</th><th scope="col">Supported source interval</th></tr></thead><tbody>{history.points.map((point) => <tr key={point.evidence_id}><th scope="row"><time dateTime={point.observed_at}>{localTime(point.observed_at)}</time></th><td>{metricValue(point.value, point.unit, units)}</td><td>{point.quality}{point.reason ? ` · ${point.reason}` : ""}</td><td>{point.support_interval ? `${localTime(point.support_interval.start)} → ${localTime(point.support_interval.end)}` : "Not supplied / instantaneous"}</td></tr>)}</tbody></table></div>
      {history.next_cursor && <Link className="button-link" to={`?${next}`}>Next observation page</Link>}
    </>}
    <p><a className="button-link" href={exportUrl} download>Export this bounded observation page as JSON</a></p><p className="small">JSON exports carry original times, units, quality and evidence. Follow the next cursor for later pages or select another source interval; query limits do not delete older history.</p>
  </>;
}
