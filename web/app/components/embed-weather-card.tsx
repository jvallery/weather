// SPDX-License-Identifier: MIT
import { useEffect, useId, useState } from "react";
import type { Current, MetricRegistry } from "../lib/contracts";
import { ageLabel, conversionAvailable, localTime, metricValue } from "../lib/format";
import { useCurrent } from "../lib/use-current";
import "./embed-weather-card.css";

const shownMetrics = [
  ["temperature", "Temperature"],
  ["wind_avg", "Average wind"],
  ["wind_gust", "Wind gust"],
  ["wind_direction", "Wind direction"],
  ["rain_today", "Rain today"],
] as const;

/** Uses Today's exact current parser, conversions, source-age logic and request cache. */
export function EmbedWeatherCard({ initial, renderedAt, registry = null, apiUnavailable = false }: {
  initial: Current;
  renderedAt: number;
  registry?: MetricRegistry | null;
  apiUnavailable?: boolean;
}) {
  const titleId = useId();
  const result = useCurrent(initial, apiUnavailable, { refreshOnResume: true, jitterFraction: 0.1 });
  const [now, setNow] = useState(renderedAt);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const current = result.data;
  const failed = result.isError || result.loaderUnavailable;
  const sourceUnits = current.metrics.some((metric) => shownMetrics.some(([id]) => id === metric.metric)
    && !conversionAvailable(metric.unit, "us", registry?.metrics.find((row) => row.metric === metric.metric)));

  return <section className="embed-weather" aria-labelledby={titleId} tabIndex={0}>
    <header><h1 id={titleId}>Current weather in Longmont</h1>
      <a href="https://weather.vallery.net/" target="_top">Open Weather</a></header>
    {failed && <p className="fault" role="status">API unavailable. Previously received measurements keep aging.</p>}
    {current.data_state === "cached" && <p>Cached measurements · original source times shown.</p>}
    {current.data_state === "unavailable" && !failed && <p>No qualified current measurements are available.</p>}
    {sourceUnits && <p>US display conversions unavailable; source units shown.</p>}
    <div className="embed-weather-metrics">{shownMetrics.map(([id, label]) => {
      const metric = current.metrics.find((row) => row.metric === id);
      const definition = registry?.metrics.find((row) => row.metric === id);
      return <article key={id}>
        <h2>{label}</h2>
        <strong>{metric ? metricValue(metric.value, metric.unit, "us", definition) : "—"}</strong>
        <p>{!metric ? "Unavailable · no measurement returned" : metric.value === null ? `Unavailable · ${metric.reason}` : ageLabel(metric, now)}</p>
        {metric?.observed_at && <p>Observed <time dateTime={metric.observed_at}>{localTime(metric.observed_at)}</time></p>}
        {metric && metric.value !== null && (metric.quality !== "valid" || metric.reason)
          && <p>{metric.reason || "Source quality is uncertain"}</p>}
      </article>;
    })}</div>
    <p className="embed-weather-note">Snapshot generated <time dateTime={current.generated_at}>{localTime(current.generated_at)}</time>. Measurements retain their own observation times.</p>
    <noscript><p>Snapshot only. This card does not auto-update without JavaScript. Open Weather for another snapshot.</p></noscript>
  </section>;
}
