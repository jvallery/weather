// SPDX-License-Identifier: MIT
import { Form, useLoaderData, type LoaderFunctionArgs } from "react-router";
import { Link } from "../components/unit-link";
import { PageHead } from "../components/common";
import { almanacSelection, COMPARISONS, hours, percent, PRODUCTS, reasonText, RECORDS, SECTORS, type AlmanacSelection } from "../lib/almanac";
import type { Units } from "../lib/contracts";
import { localDate, localTime, metricValue } from "../lib/format";
import { almanacRead, metricRegistryRead } from "../lib/public-api.server";
import { unitsFrom } from "../lib/route-params";
import type { Almanac, AstronomyItem, ComparisonItem, ComparisonWindow, MetricDefinition, MetricRegistry, RainDayItem, RecordItem, WindRoseBinItem } from "../lib/weather-api";
export const meta = () => [{ title: "Local almanac · Weather" }];

export async function loader({ request }: LoaderFunctionArgs) {
  const url = new URL(request.url);
  const { selection, params } = almanacSelection(url, Date.now());
  // Two reads per document, within the SSR API admission budget.
  const [result, registry] = await Promise.all([almanacRead(request, params), metricRegistryRead(request)]);
  return { almanac: result.kind === "ok" ? result.data : null, rejected: result.kind === "invalid", registry, selection, units: unitsFrom(url) };
}

const clock = new Intl.DateTimeFormat("en-US", { timeZone: "America/Denver", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZoneName: "shortOffset" });
function Clock({ instant }: { instant: string | null }) {
  return instant ? <time dateTime={instant}>{clock.format(new Date(instant))}</time> : <>—</>;
}
function definitionFor(registry: MetricRegistry | null, metric: string | null): MetricDefinition | undefined {
  return registry?.metrics.find((item) => item.metric === metric);
}
type View = { almanac: Almanac; units: Units; definition?: MetricDefinition; registry: MetricRegistry | null };

function Records({ almanac, units, definition }: View) {
  const items = almanac.items.filter((item): item is RecordItem => item.product === "record");
  const ranked = items.filter((item) => item.rank !== null).sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0)).slice(0, 10);
  const value = (item: RecordItem) => metricValue(item.value, almanac.unit || "", units, definition);
  const status = (item: RecordItem) => item.rank !== null ? `Rank ${item.rank}` : reasonText(item.missing_reasons) || "Not ranked";
  return <section aria-labelledby="records-heading"><h2 id="records-heading">{almanac.label}</h2>
    <p className="small">Only local days with at least 95% coverage are ranked; ties share a rank. Days with less coverage stay listed below with their values, outside the ranking.</p>
    {ranked.length === 0 ? <p>No day in these dates has enough coverage to rank.</p> : <div className="table-wrap" tabIndex={0} role="region" aria-label="Ranked days"><table><caption>Top ranked days</caption><thead><tr><th scope="col">Rank</th><th scope="col">Local date</th><th scope="col">Value</th><th scope="col">Coverage</th></tr></thead>
      <tbody>{ranked.map((item) => <tr key={item.start}><td>{item.rank}</td><th scope="row">{item.local_date}</th><td>{value(item)}</td><td>{percent(item.coverage.fraction)}</td></tr>)}</tbody></table></div>}
    <details><summary>Every day in the selection ({items.length})</summary><div className="table-wrap" tabIndex={0} role="region" aria-label="Every day"><table><caption>Observed daily values</caption><thead><tr><th scope="col">Local date</th><th scope="col">Value</th><th scope="col">Coverage</th><th scope="col">Status</th></tr></thead>
      <tbody>{items.map((item) => <tr key={item.start}><th scope="row">{item.local_date}</th><td>{value(item)}</td><td>{percent(item.coverage.fraction)}</td><td>{status(item)}</td></tr>)}</tbody></table></div></details>
  </section>;
}

function rainLevel(item: RainDayItem): string {
  if (item.value === null) return "rain-none";
  if (item.value === 0) return "rain-dry";
  return item.value < 2.5 ? "rain-light" : item.value < 10 ? "rain-moderate" : "rain-heavy";
}
function Rain({ almanac, units, definition }: View) {
  const items = almanac.items.filter((item): item is RainDayItem => item.product === "rain_day");
  const amount = (value: number | null) => metricValue(value, "mm", units, definition);
  const observed = items.reduce((total, item) => total + (item.value || 0), 0);
  return <section aria-labelledby="rain-heading"><h2 id="rain-heading">Rain calendar</h2>
    <p>{almanac.label}. Observed total over these days: <strong>{amount(observed)}</strong>{almanac.coverage && <> at {percent(almanac.coverage.fraction)} coverage</>}.</p>
    <ol className="calendar rain-calendar" aria-hidden="true">{items.map((item) => <li key={item.start} className={`${rainLevel(item)}${item.complete ? "" : " rain-partial"}`}><span>{item.local_date.slice(5)}</span><small>{item.value === null ? "no data" : amount(item.value)}</small></li>)}</ol>
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Rain by local day"><table><caption>Rain by local day (text alternative to the calendar)</caption><thead><tr><th scope="col">Local date</th><th scope="col">Observed rain</th><th scope="col">Coverage</th><th scope="col">Notes</th></tr></thead>
      <tbody>{items.map((item) => <tr key={item.start}><th scope="row">{item.local_date}</th><td>{item.value === null ? "—" : amount(item.value)}</td><td>{percent(item.coverage.fraction)}</td><td>{reasonText(item.missing_reasons) || "Complete"}</td></tr>)}</tbody></table></div>
  </section>;
}

const RADIUS = 140;
function wedge(sector: number, inner: number, outer: number): string {
  const point = (angle: number, radius: number) => { const r = (angle * Math.PI) / 180; return `${(radius * Math.sin(r)).toFixed(2)} ${(-radius * Math.cos(r)).toFixed(2)}`; };
  const a = sector * 22.5 - 11.25, b = sector * 22.5 + 11.25;
  return `M${point(a, inner)}L${point(a, outer)}A${outer} ${outer} 0 0 1 ${point(b, outer)}L${point(b, inner)}A${inner} ${inner} 0 0 0 ${point(a, inner)}Z`;
}
function Rose({ almanac, units, registry }: View) {
  const bins = almanac.items.filter((item): item is WindRoseBinItem => item.product === "wind_rose_bin");
  const speed = definitionFor(registry, "wind_avg");
  const speeds = [...new Set(bins.filter((b) => b.category === "directional").map((b) => b.speed_min))];
  const directional = (sector: number) => bins.filter((b) => b.category === "directional" && b.sector_start !== null && Math.round(((b.sector_start + 11.25) % 360) / 22.5) % 16 === sector);
  const totals = SECTORS.map((_, sector) => directional(sector).reduce((sum, b) => sum + b.duration_seconds, 0));
  const denominator = bins[0]?.denominator_seconds || 1;
  const largest = Math.max(...totals, 1);
  const label = (b: WindRoseBinItem) => `${metricValue(b.speed_min, "m/s", units, speed)}${b.speed_max === null ? " and above" : ` to under ${metricValue(b.speed_max, "m/s", units, speed)}`}`;
  const share = (seconds: number) => percent(seconds / denominator);
  const other = (category: string) => bins.find((b) => b.category === category)?.duration_seconds || 0;
  return <section aria-labelledby="rose-heading"><h2 id="rose-heading">Wind rose</h2>
    <p>{almanac.label}. Shares are of the whole selected time ({hours(denominator)}), including missing time, so they are never renormalized to 100% of observed wind.</p>
    <svg className="wind-rose" viewBox="-160 -160 320 320" role="img" aria-labelledby="rose-title"><title id="rose-title">Wind rose: share of time the wind blew from each of 16 directions, by speed. The table below lists every value.</title>
      {[0.25, 0.5, 0.75, 1].map((ring) => <circle key={ring} className="rose-ring" r={RADIUS * ring} />)}
      {SECTORS.map((name, sector) => { let inner = 0; return <g key={name}>{directional(sector).map((b, index) => { const outer = inner + (RADIUS * b.duration_seconds) / largest; const path = b.duration_seconds > 0 ? <path key={index} className={`rose-speed-${speeds.indexOf(b.speed_min)}`} d={wedge(sector, inner, outer)} /> : null; inner = outer; return path; })}</g>; })}
      {["N", "E", "S", "W"].map((name, index) => <text key={name} className="rose-label" x={[0, 150, 0, -150][index]} y={[-146, 5, 156, 5][index]} textAnchor="middle">{name}</text>)}
    </svg>
    <ul className="rose-legend">{speeds.map((value, index) => { const sample = bins.find((b) => b.category === "directional" && b.speed_min === value); return sample ? <li key={index}><span className={`rose-key rose-speed-${index}`} aria-hidden="true" />{label(sample)}</li> : null; })}</ul>
    <p>The outer ring marks {share(largest)} of the selected time in one direction. Calm: {share(other("calm"))} · Variable: {share(other("variable"))} · Missing: {share(other("missing"))}</p>
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Wind rose table"><table><caption>Share of the selected time by direction and speed (text alternative to the rose)</caption>
      <thead><tr><th scope="col">From</th>{speeds.map((value, index) => { const sample = bins.find((b) => b.category === "directional" && b.speed_min === value); return <th scope="col" key={index}>{sample ? label(sample) : ""}</th>; })}<th scope="col">All speeds</th></tr></thead>
      <tbody>{SECTORS.map((name, sector) => <tr key={name}><th scope="row">{name}</th>{directional(sector).map((b, index) => <td key={index}>{share(b.duration_seconds)}</td>)}<td>{share(totals[sector])}</td></tr>)}
        {["calm", "variable", "missing"].map((category) => <tr key={category}><th scope="row">{category[0].toUpperCase() + category.slice(1)}</th><td colSpan={speeds.length}>—</td><td>{share(other(category))}</td></tr>)}</tbody></table></div>
  </section>;
}

const PHASES: Record<string, string> = { new: "New moon", waxing_crescent: "Waxing crescent", first_quarter: "First quarter", waxing_gibbous: "Waxing gibbous", full: "Full moon", waning_gibbous: "Waning gibbous", last_quarter: "Last quarter", waning_crescent: "Waning crescent" };
function Astronomy({ almanac }: View) {
  const items = almanac.items.filter((item): item is AstronomyItem => item.product === "astronomy");
  return <section aria-labelledby="sky-heading"><h2 id="sky-heading">Sun and moon · derived</h2>
    <p>{almanac.label}. Civil dawn and dusk use the sun 6° below the horizon, the same boundaries that split daytime and overnight timelapses.</p>
    {almanac.missing_reasons.includes("site_not_configured") && <p className="notice">Sun and moon rise and set times are unavailable until the station site is configured. Moon phase does not depend on the site and is shown.</p>}
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Sun and moon times"><table><caption>Derived sun and moon times, America/Denver</caption><thead><tr><th scope="col">Local date</th><th scope="col">Civil dawn</th><th scope="col">Sunrise</th><th scope="col">Solar noon</th><th scope="col">Sunset</th><th scope="col">Civil dusk</th><th scope="col">Day length</th><th scope="col">Moon</th><th scope="col">Moonrise</th><th scope="col">Moonset</th></tr></thead>
      <tbody>{items.map((item) => <tr key={item.start}><th scope="row">{item.local_date}</th><td><Clock instant={item.civil_dawn} /></td><td><Clock instant={item.sunrise} /></td><td><Clock instant={item.solar_noon} /></td><td><Clock instant={item.sunset} /></td><td><Clock instant={item.civil_dusk} /></td><td>{item.day_length_seconds === null ? "—" : hours(item.day_length_seconds)}</td><td>{item.moon_phase_name ? PHASES[item.moon_phase_name] : "—"}</td><td><Clock instant={item.moonrise} /></td><td><Clock instant={item.moonset} /></td></tr>)}</tbody></table></div>
  </section>;
}

function WindowCells({ window, render }: { window: ComparisonWindow; render: (value: number | null) => string }) {
  return <>{[
    `${localDate(window.start)} to ${localDate(new Date(Date.parse(window.end) - 1).toISOString())}`,
    hours(window.duration_seconds),
    render(window.value),
    percent(window.coverage.fraction),
    window.missing_days.join(", ") || "None",
    window.partial_days.join(", ") || "None",
    window.adequate ? "Yes" : `No · ${reasonText(window.missing_reasons)}`,
  ].map((text, index) => <td key={index}>{text}</td>)}</>;
}
function Comparison({ almanac, units, definition }: View) {
  const item = almanac.items.find((entry): entry is ComparisonItem => entry.product === "comparison");
  if (!item) return <p>No comparison was returned.</p>;
  const render = (value: number | null) => metricValue(value, almanac.unit || "", units, definition);
  const rows = ["Local dates", "Actual duration", "Observed value", "Coverage", "Days without data", "Partially covered days", "Adequate (≥95% coverage)"];
  return <section aria-labelledby="compare-heading"><h2 id="compare-heading">Compare periods</h2>
    <p>{almanac.label}. Each period keeps its own duration and coverage; nothing is interpolated or rescaled.</p>
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Period comparison"><table><caption>{almanac.metric} · {almanac.statistic}</caption><thead><tr><th scope="col">Period</th>{rows.map((row) => <th scope="col" key={row}>{row}</th>)}</tr></thead>
      <tbody><tr><th scope="row">First</th><WindowCells window={item.first} render={render} /></tr><tr><th scope="row">Second</th><WindowCells window={item.second} render={render} /></tr></tbody></table></div>
    <p>{item.difference !== null ? <>Second minus first: <strong>{item.difference > 0 ? "+" : ""}{metricValue(item.difference, almanac.unit || "", units, definition, true)}</strong></> : "No difference is shown because a period has less than 95% coverage."}</p>
  </section>;
}

const VIEWS = { record: Records, rain_day: Rain, wind_rose_bin: Rose, astronomy: Astronomy, comparison: Comparison } as const;
function Filters({ selection, units }: { selection: AlmanacSelection; units: Units }) {
  return <Form method="get" className="filters"><input type="hidden" name="product" value={selection.product} />{units === "metric" && <input type="hidden" name="units" value="metric" />}
    <label>First local date<input type="date" name="from" defaultValue={selection.from} required /></label><label>Last local date<input type="date" name="to" defaultValue={selection.to} required /></label>
    {selection.product === "record" && <label>Record<select name="record" defaultValue={selection.record}>{Object.entries(RECORDS).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}</select></label>}
    {selection.product === "comparison" && <><label>Compare with first date<input type="date" name="compare_from" defaultValue={selection.compareFrom} required /></label><label>Compare with last date<input type="date" name="compare_to" defaultValue={selection.compareTo} required /></label><label>Value<select name="compare" defaultValue={selection.compare}>{Object.entries(COMPARISONS).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}</select></label></>}
    <button type="submit">Show</button></Form>;
}

export default function AlmanacPage() {
  const { almanac, rejected, registry, selection, units } = useLoaderData<typeof loader>();
  const View = VIEWS[selection.product];
  const definition = definitionFor(registry, almanac?.metric ?? null);
  const history = almanac?.history_interval;
  return <><PageHead eyebrow="Almanac · this station's own record" title="Local almanac" lede="Records, rain, wind and comparisons from this station's observations, computed when the page loads. They are not climate normals. Sun and moon times are derived, not measured." />
    <nav aria-label="Almanac sections"><ul className="chips">{PRODUCTS.map((item) => <li key={item.id}><Link to={`?product=${item.id}${units === "metric" ? "&units=metric" : ""}`} aria-current={item.id === selection.product ? "page" : undefined}>{item.label}</Link></li>)}</ul></nav>
    <Filters selection={selection} units={units} />
    {rejected ? <p className="fault">The API rejected this almanac selection. Choose whole local dates within 366 days.</p>
      : !almanac ? <p className="fault">Almanac API unavailable. No substitute values are displayed.</p>
      : <>
        <p className="notice">{history ? <>Station history since <time dateTime={history.start}>{localDate(history.start)}</time> (first observation {localTime(history.start)}).</> : <>No station history yet. Observed products will fill in as the station reports.</>}{almanac.coverage && <> Coverage of the selected dates: {percent(almanac.coverage.fraction)}.</>}</p>
        <View almanac={almanac} units={units} definition={definition} registry={registry} />
        <p className="small provenance">Formula {almanac.formula_revision} · {almanac.library_revision} · {almanac.evidence_count.toLocaleString("en-US")} source rows{almanac.evidence_hash && <> · evidence <span className="monospace">{almanac.evidence_hash.slice(0, 12)}</span></>} · computed {localTime(almanac.generated_at)}</p>
      </>}
  </>;
}
