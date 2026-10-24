// SPDX-License-Identifier: MIT
import { useEffect, useState } from "react";
import type { Current, Units } from "../lib/contracts";
import { ageLabel, localTime, metricLabel, metricValue } from "../lib/format";
import { useCurrent } from "../lib/use-current";

export function CurrentConditions({ initial, renderedAt, units }: { initial: Current; renderedAt: number; units: Units }) {
  const result = useCurrent(initial);
  const [now, setNow] = useState(renderedAt);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const current = result.data;
  return <section aria-labelledby="conditions-title"><h2 id="conditions-title">Current conditions</h2>
    <p className="screen-reader" role="status" aria-live="polite">Current conditions refresh about every 15 seconds while this page is visible.</p>
    {(result.isError || result.loaderUnavailable || current.data_state === "unavailable") && <p className="fault">API unavailable. Received values keep their original source times and continue aging.</p>}
    {current.data_state === "cached" && <p className="notice">Last known measurements · the station feed is unavailable.</p>}
    {current.metrics.length === 0 ? <p>No measurements are available yet. Station history starts when direct reception is commissioned.</p> : <div className="metrics">{current.metrics.map((metric) => <article className="metric" key={metric.metric}>
      <h3>{metricLabel(metric.metric)}</h3><strong>{metricValue(metric.value, metric.unit, units)}</strong>
      <p>{metric.value === null ? `Unavailable · ${metric.reason}` : ageLabel(metric, now)}</p>
      {metric.observed_at && <p className="small">Observed <time dateTime={metric.observed_at}>{localTime(metric.observed_at)}</time></p>}
      {metric.quality !== "valid" && metric.value !== null && <p className="fault">{metric.quality} · {metric.reason || "Source quality is uncertain"}</p>}
      <span className="provenance">Measured · expected interval {metric.expected_interval_seconds} s</span>
    </article>)}</div>}
  </section>;
}
