// SPDX-License-Identifier: MIT
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";
import fixture from "../fixtures/current.json";
import registryFixture from "../fixtures/metric-registry.json";
import { parseCurrent, parseRegistry, type Current } from "../lib/weather-api";
import { CurrentConditions } from "./current-conditions";
import { EmbedUnavailableCard, EmbedWeatherCard } from "./embed-weather-card";

const current = parseCurrent(fixture)!;
const registry = parseRegistry(registryFixture)!;
function ssr(node: ReactNode) {
  const client = new QueryClient();
  try { return renderToStaticMarkup(<QueryClientProvider client={client}>{node}</QueryClientProvider>); } finally { client.clear(); }
}
const card = (initial: Current, renderedAt: number, apiUnavailable = false) => ssr(<EmbedWeatherCard initial={initial}
  renderedAt={renderedAt} registry={registry} apiUnavailable={apiUnavailable} />);
const metric = (name: string, value: number, unit: string, observedAt: string, validUntil: string, freshness: "fresh" | "stale") => ({
  metric: name, value, unit, observed_at: observedAt, received_at: observedAt, expected_interval_seconds: 60,
  valid_until: validUntil, quality: "valid", freshness, reason: null, coverage: name === "rain_today" ? 1 : null,
});
// Exactly the card's five metrics, in its order, one of them an hour old.
const parity = parseCurrent({ ...fixture, metrics: [
  metric("temperature", 21.5, "degC", "2026-11-01T08:15:00Z", "2026-11-01T08:16:30Z", "fresh"),
  metric("wind_avg", 3.2, "m/s", "2026-11-01T08:15:00Z", "2026-11-01T08:16:30Z", "fresh"),
  metric("wind_gust", 5, "m/s", "2026-11-01T07:15:00Z", "2026-11-01T07:16:30Z", "stale"),
  metric("wind_direction", 270, "deg", "2026-11-01T08:15:00Z", "2026-11-01T08:16:30Z", "fresh"),
  metric("rain_today", 2.54, "mm", "2026-11-01T08:15:00Z", "2026-11-01T08:16:30Z", "fresh"),
] })!;
/** Value, age/freshness line and observed_at of each rendered metric, in order. */
function readings(html: string) {
  return [...html.matchAll(/<strong>([^<]*)<\/strong><p>([^<]*)<\/p><p[^>]*>Observed <time dateTime="([^"]+)"/g)].map((match) => match.slice(1));
}
/** The same triple from Today's "All readings" table (value, observed time, age). */
function todayReadings(html: string) {
  return [...html.matchAll(/<td>([^<]*)<\/td><td><time dateTime="([^"]+)"[^>]*>[^<]*<\/time><\/td><td>([^<]*)<\/td>/g)].map(([, value, observed, age]) => [value, age, observed]);
}

test("card and Today show the same values, units, ages and observed_at from one fixture", () => {
  for (const now of [Date.parse(parity.generated_at), Date.parse(parity.generated_at) + 600_000]) {
    const embedded = readings(card(parity, now));
    const today = todayReadings(ssr(<CurrentConditions initial={parity} renderedAt={now} units="us" registry={registry} />));
    expect(embedded).toHaveLength(5);
    expect(embedded).toEqual(today);
  }
  const [temperature, wind, gust, direction, rain] = readings(card(parity, Date.parse(parity.generated_at)));
  expect(temperature).toEqual(["70.7 °F", "5 s old", "2026-11-01T08:15:00Z"]);
  expect(wind[0]).toBe("7.2 mph");
  expect(direction[0]).toBe("270 °");
  expect(rain[0]).toBe("0.1 in");
  // An hour-old observation is labelled stale on both surfaces, never current.
  expect(gust).toEqual(["11.2 mph", "1 h old · stale", "2026-11-01T07:15:00Z"]);
});

test("a fresh value turns stale on the clock alone, and missing metrics stay unavailable", () => {
  const html = card(current, Date.parse(current.generated_at) + 600_000);
  expect(html).toContain("10 min old · stale");
  expect(html).not.toMatch(/\d+ (?:s|min) old<\/p>/);
  for (const label of ["Average wind", "Wind direction", "Rain today"]) expect(html).toContain(label);
  expect(html.match(/Unavailable · no measurement returned/g)).toHaveLength(3);
  expect(html).not.toContain("Humidity");
});

test("without JavaScript the snapshot shows its time and never claims to auto-update", () => {
  const html = card(current, Date.parse(current.generated_at));
  expect(html).toContain(`Snapshot generated <time dateTime="${current.generated_at}">`);
  expect(html).toContain("<noscript><p>Snapshot only. This card does not auto-update without JavaScript.");
  expect(html.replace(/<noscript>.*<\/noscript>/, "")).not.toMatch(/refresh|update|live/i);
  expect(html).toContain('<a href="https://weather.vallery.net/" target="_top">Open Weather</a>');
  expect(html).not.toContain(current.station_alias);
});

test("a server-side API failure keeps the snapshot's source times and says the API is unavailable", () => {
  const html = card(current, Date.parse(current.generated_at), true);
  expect(html).toContain("API unavailable. Previously received measurements keep aging.");
  expect(html).toContain('dateTime="2026-11-01T08:15:00Z"');
});

test("the error card shows no values and keeps the top-level exit link", () => {
  const html = ssr(<EmbedUnavailableCard />);
  expect(html).toContain("Current weather is unavailable right now. No measurements are shown.");
  expect(html).toContain('target="_top"');
  expect(html).not.toMatch(/<strong>|<time/);
});

test("an unparseable observed_at shows that time as unavailable instead of failing the render", () => {
  const now = Date.parse("2026-10-24T18:00:00Z");
  const broken = parseCurrent({ ...fixture, metrics: [metric("temperature", 4.3, "degC", "not-a-time", "2026-10-24T18:02:00Z", "fresh")] })!;
  const today = ssr(<CurrentConditions initial={broken} renderedAt={now} units="us" registry={registry} />);
  const embed = card(broken, now);
  for (const html of [today, embed]) expect(html).toContain("time unavailable");
});
