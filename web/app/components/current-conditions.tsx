// SPDX-License-Identifier: MIT
import { useEffect, useState } from "react";
import type { Units } from "../lib/contracts";
import type { Current, Metric, MetricDefinition, MetricRegistry } from "../lib/weather-api";
import { ageLabel, ageSeconds, compass, conversionAvailable, duration, humanize, localTime, metricLabel, metricValue } from "../lib/format";
import { useCurrent } from "../lib/use-current";

type Lookup = (name: string) => { metric: Metric; definition?: MetricDefinition } | null;
const isStale = (metric: Metric, now: number) => metric.freshness !== "fresh" || !metric.valid_until || Date.parse(metric.valid_until) <= now;
const SUMMED = ["rain_last_hour", "rain_today"];

/** The curated strip: what a visitor scans for. Every metric, curated or not, is in "All readings". */
function strip(get: Lookup, units: Units): { key: string; label: string; value: string; note?: string; metric: Metric }[] {
  const value = (name: string) => { const found = get(name); return found ? metricValue(found.metric.value, found.metric.unit, units, found.definition) : null; };
  const rows: { key: string; label: string; value: string; note?: string; metric: Metric }[] = [];
  const add = (key: string, label: string, note?: (metric: Metric) => string | undefined) => {
    const found = get(key);
    if (found) rows.push({ key, label, value: value(key)!, note: note?.(found.metric), metric: found.metric });
  };
  add("humidity", "Humidity");
  add("wind_avg", "Wind", () => { const direction = get("wind_direction")?.metric.value ?? null; const name = compass(direction); return name ? `from ${name} (${Math.round(direction!)}°)` : undefined; });
  add("wind_gust", "Gust", () => { const lull = value("wind_lull"); return lull ? `lull ${lull}` : undefined; });
  add("station_pressure", "Pressure", () => "station level");
  add("rain_today", "Rain today", () => { const hour = value("rain_last_hour"); return hour ? `${hour} last hour` : undefined; });
  add("uv", "UV index");
  add("irradiance", "Solar");
  const strikes = get("lightning_count");
  if (strikes && strikes.metric.value !== null && strikes.metric.value > 0) {
    const distance = get("lightning_distance");
    rows.push({ key: "lightning_count", label: "Lightning", value: `${strikes.metric.value}`, note: distance?.metric.value != null ? `about ${metricValue(distance.metric.value, distance.metric.unit, units, distance.definition)} away` : "strikes reported", metric: strikes.metric });
  }
  return rows;
}

export function CurrentConditions({ initial, renderedAt, units, registry = null, apiUnavailable = false }: { initial: Current; renderedAt: number; units: Units; registry?: MetricRegistry | null; apiUnavailable?: boolean }) {
  const result = useCurrent(initial, apiUnavailable);
  const [now, setNow] = useState(renderedAt);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const current = result.data;
  const definition = (name: string) => registry?.metrics.find((row) => row.metric === name);
  const get: Lookup = (name) => { const metric = current.metrics.find((item) => item.metric === name); return metric ? { metric, definition: definition(name) } : null; };
  const temperature = get("temperature") ?? get("air_temperature");
  const ages = current.metrics.map((metric) => ageSeconds(metric.observed_at, now)).filter((age): age is number => age !== null);
  const newest = ages.length ? Math.min(...ages) : null;
  const anyStale = current.metrics.some((metric) => metric.value !== null && isStale(metric, now));
  const down = result.isError || result.loaderUnavailable;
  const stats = strip(get, units);
  return <section className="hero" aria-labelledby="conditions-title">
    <div>
      <span className="eyebrow">Longmont · backyard station</span>
      <h1 id="conditions-title">Current conditions</h1>
      <p className="screen-reader" role="status" aria-live="polite">Current conditions refresh about every 15 seconds while this page is visible.</p>
      {temperature && <p className="hero-temp" data-metric="temperature">{metricValue(temperature.metric.value, temperature.metric.unit, units, temperature.definition)}</p>}
      {newest !== null && <p className="hero-meta"><span className={`dot${down ? " dot-off" : anyStale ? " dot-stale" : ""}`} aria-hidden="true" />Updated {duration(newest)} ago{anyStale ? " · some readings are stale" : ""}</p>}
      {down && <p className="fault">API unavailable. Received values keep their original source times and continue aging.</p>}
      {current.data_state === "cached" && <p className="notice">Serving cached measurements; their source times keep aging.</p>}
      {current.data_state === "unavailable" && !down && <p className="notice">No qualified current measurements are available.</p>}
      {current.metrics.some((metric) => !conversionAvailable(metric.unit, units, definition(metric.metric))) && <p className="notice">Display conversions unavailable; source units shown.</p>}
    </div>
    {current.metrics.length === 0 ? <p>No current measurements were returned. No substitute values are displayed.</p> : <>
      <dl className="stats">{stats.map((row) => <div className="stat" key={row.key} data-metric={row.key}><dt>{row.label}</dt><dd>{row.value}{row.note && <small>{row.note}</small>}
        {row.metric.value === null ? <span className="tag tag-fault">Unavailable</span> : row.metric.quality !== "valid" ? <span className="tag tag-fault">{humanize(row.metric.quality)}</span> : isStale(row.metric, now) ? <span className="tag tag-warn">Stale</span> : null}</dd></div>)}</dl>
      <details><summary>All readings · observation times and cadence</summary>
        <div className="table-wrap" tabIndex={0} role="region" aria-label="All current readings"><table><caption className="screen-reader">Every current reading with its observation time</caption>
          <thead><tr><th scope="col">Reading</th><th scope="col">Value</th><th scope="col">Observed · America/Denver</th><th scope="col">Age</th><th scope="col">Basis</th><th scope="col">Notes</th></tr></thead>
          <tbody>{current.metrics.map((metric) => <tr key={metric.metric}>
            <th scope="row">{metricLabel(metric.metric, definition(metric.metric))}</th>
            <td>{metricValue(metric.value, metric.unit, units, definition(metric.metric))}</td>
            <td>{metric.observed_at ? <time dateTime={metric.observed_at}>{localTime(metric.observed_at)}</time> : "—"}</td>
            <td>{ageLabel(metric, now)}</td>
            <td>{SUMMED.includes(metric.metric) ? `Summed from reports${metric.coverage !== null ? ` · ${Math.round(metric.coverage * 100)}% reported` : ""}` : "Measured"}{metric.expected_interval_seconds === null ? " · cadence not commissioned" : ` · every ${metric.expected_interval_seconds} s`}</td>
            <td>{metric.value === null ? `Unavailable · ${humanize(metric.reason)}` : metric.quality !== "valid" || metric.reason ? `${metric.quality !== "valid" ? `${humanize(metric.quality)} · ` : ""}${metric.reason ? humanize(metric.reason) : "Source quality is uncertain"}` : ""}</td>
          </tr>)}</tbody></table></div>
      </details>
    </>}
  </section>;
}
