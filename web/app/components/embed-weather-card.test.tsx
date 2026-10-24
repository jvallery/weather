// SPDX-License-Identifier: MIT
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";
import fixture from "../fixtures/current.json";
import registryFixture from "../fixtures/metric-registry.json";
import { currentSchema, metricRegistrySchema } from "../lib/contracts";
import { CurrentConditions } from "./current-conditions";
import { EmbedWeatherCard } from "./embed-weather-card";

const current = currentSchema.parse(fixture);
const registry = metricRegistrySchema.parse(registryFixture);
function card(renderedAt: number, apiUnavailable = false) {
  const client = new QueryClient();
  try {
    return renderToStaticMarkup(<QueryClientProvider client={client}><EmbedWeatherCard initial={current}
      renderedAt={renderedAt} registry={registry} apiUnavailable={apiUnavailable} /></QueryClientProvider>);
  } finally { client.clear(); }
}

test("the snapshot uses Today's values, US conversion recipe and original source time", () => {
  const now = Date.parse(current.generated_at);
  const metric = current.metrics.find((row) => row.metric === "temperature")!;
  const html = card(now);
  const client = new QueryClient();
  let today: string;
  try {
    today = renderToStaticMarkup(<QueryClientProvider client={client}><CurrentConditions
      initial={current} renderedAt={now} registry={registry} units="us" /></QueryClientProvider>);
  } finally { client.clear(); }
  for (const rendered of [html, today]) {
    expect(rendered).toContain("32 °F");
    expect(rendered).toContain("5 s old");
    expect(rendered).toContain(`dateTime="${metric.observed_at}"`);
  }
  expect(html).toContain(`dateTime="${metric.observed_at}"`);
  expect(html).toContain('target="_top"');
  expect(html).toContain("does not auto-update without JavaScript");
  expect(html).not.toContain("Humidity");
});

test("an old source expires without a new API response and missing fields stay unavailable", () => {
  const html = card(Date.parse(current.generated_at) + 600_000);
  expect(html).toContain("10 min old · stale");
  expect(html).toContain("Average wind");
  expect(html).toContain("Unavailable · no measurement returned");
});

test("a server-side API failure retains the snapshot while labelling transport unavailable", () => {
  const html = card(Date.parse(current.generated_at), true);
  expect(html).toContain("API unavailable");
  expect(html).toContain("Previously received measurements keep aging");
  expect(html).toContain('dateTime="2026-11-01T08:15:00Z"');
});
