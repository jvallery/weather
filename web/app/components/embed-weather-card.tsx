// SPDX-License-Identifier: MIT
import { useEffect, useState } from "react";
import type { Units } from "../lib/contracts";
import type { Current, MetricRegistry } from "../lib/weather-api";
import { ageLabel, conversionAvailable, localTime, metricValue } from "../lib/format";
import { useCurrent } from "../lib/use-current";

const shownMetrics = [
  ["temperature", "Temperature"],
  ["wind_avg", "Average wind"],
  ["wind_gust", "Wind gust"],
  ["wind_direction", "Wind direction"],
  ["rain_today", "Rain today"],
] as const;

function Heading() {
  // The only link leaves the frame for the full site (D13 parent contract).
  return <header><h1 id="embed-weather-title">Current weather in Longmont</h1>
    <a href="https://weather.vallery.net/" target="_top">Open Weather</a></header>;
}

/** Today's parser, conversions, source-age logic and request cache, with the embed polling options. */
export function EmbedWeatherCard({ initial, renderedAt, units = "us", registry = null, apiUnavailable = false }: {
  initial: Current;
  renderedAt: number;
  units?: Units;
  registry?: MetricRegistry | null;
  apiUnavailable?: boolean;
}) {
  const result = useCurrent(initial, apiUnavailable, { refreshOnResume: true, jitterFraction: 0.1 });
  const [now, setNow] = useState(renderedAt);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const current = result.data;
  const failed = result.isError || result.loaderUnavailable;
  const sourceUnits = current.metrics.some((metric) => shownMetrics.some(([id]) => id === metric.metric)
    && !conversionAvailable(metric.unit, units, registry?.metrics.find((row) => row.metric === metric.metric)));

  return <main className="embed-weather" aria-labelledby="embed-weather-title" tabIndex={0}>
    <Heading />
    {failed && <p className="fault" role="status">API unavailable. Previously received measurements keep aging.</p>}
    {current.data_state === "cached" && <p>Cached measurements · original source times shown.</p>}
    {current.data_state === "unavailable" && !failed && <p>No qualified current measurements are available.</p>}
    {sourceUnits && <p>Display conversions unavailable; source units shown.</p>}
    <div className="embed-weather-metrics">{shownMetrics.map(([id, label]) => {
      const metric = current.metrics.find((row) => row.metric === id);
      const definition = registry?.metrics.find((row) => row.metric === id);
      return <article key={id}>
        <h2>{label}</h2>
        <strong>{metric ? metricValue(metric.value, metric.unit, units, definition) : "—"}</strong>
        <p>{!metric ? "Unavailable · no measurement returned" : metric.value === null ? `Unavailable · ${metric.reason || "no measurement"}` : ageLabel(metric, now)}</p>
        {metric?.observed_at && <p>Observed <time dateTime={metric.observed_at}>{localTime(metric.observed_at)}</time></p>}
        {metric && metric.value !== null && (metric.quality !== "valid" || metric.reason)
          && <p>{metric.reason || "Source quality is uncertain"}</p>}
      </article>;
    })}</div>
    <p className="embed-weather-note">Snapshot generated <time dateTime={current.generated_at}>{localTime(current.generated_at)}</time>. Measurements retain their own observation times.</p>
    <noscript><p>Snapshot only. This card does not auto-update without JavaScript. Open Weather for another snapshot.</p></noscript>
  </main>;
}

/** Route errors (404, 500, overload) inside the frame: no values, the same exit link. */
export function EmbedUnavailableCard() {
  return <main className="embed-weather" aria-labelledby="embed-weather-title" tabIndex={0}>
    <Heading />
    <p className="fault">Current weather is unavailable right now. No measurements are shown.</p>
  </main>;
}
