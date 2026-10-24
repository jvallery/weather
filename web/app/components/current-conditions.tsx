// SPDX-License-Identifier: MIT
import { useEffect, useState } from "react";
import type { Current, MetricRegistry, Units } from "../lib/contracts";
import { ageLabel, conversionAvailable, localTime, metricLabel, metricValue } from "../lib/format";
import { useCurrent } from "../lib/use-current";

export function CurrentConditions({ initial, renderedAt, units, registry = null, apiUnavailable = false }: { initial: Current; renderedAt: number; units: Units; registry?: MetricRegistry | null; apiUnavailable?: boolean }) {
  const result = useCurrent(initial, apiUnavailable);
  const [now, setNow] = useState(renderedAt);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const current = result.data;
  return <section aria-labelledby="conditions-title"><h2 id="conditions-title">Current conditions</h2>
    <p className="screen-reader" role="status" aria-live="polite">Current conditions refresh about every 15 seconds while this page is visible.</p>
    {(result.isError || result.loaderUnavailable) && <p className="fault">API unavailable. Received values keep their original source times and continue aging.</p>}
    {current.data_state === "cached" && <p className="notice">API serving cached measurements · original source times continue aging.</p>}
    {current.data_state === "unavailable" && !result.isError && !result.loaderUnavailable && <p className="notice">No qualified current measurements are available.</p>}
    {current.metrics.some((metric) => !conversionAvailable(metric.unit, units, registry?.metrics.find((row) => row.metric === metric.metric))) && <p className="notice">Display conversions unavailable; source units shown.</p>}
    {current.metrics.length === 0 ? <p>No current measurements were returned. No substitute values are displayed.</p> : <div className="metrics">{current.metrics.map((metric) => { const definition = registry?.metrics.find((row) => row.metric === metric.metric); return <article className="metric" key={metric.metric}>
      <h3>{metricLabel(metric.metric, definition)}</h3><strong>{metricValue(metric.value, metric.unit, units, definition)}</strong>
      <p>{metric.value === null ? `Unavailable · ${metric.reason}` : ageLabel(metric, now)}</p>
      {metric.observed_at && <p className="small">Observed <time dateTime={metric.observed_at}>{localTime(metric.observed_at)}</time></p>}
      {metric.value !== null && (metric.quality !== "valid" || metric.reason) && <p className="fault">{metric.quality !== "valid" ? `${metric.quality} · ` : ""}{metric.reason || "Source quality is uncertain"}</p>}
      <span className="provenance">{["rain_last_hour", "rain_today"].includes(metric.metric) ? "Derived from measurements" : "Measured"} · {metric.expected_interval_seconds === null ? "Observation cadence not commissioned" : `expected interval ${metric.expected_interval_seconds} s`}</span>
    </article>; })}</div>}
  </section>;
}
