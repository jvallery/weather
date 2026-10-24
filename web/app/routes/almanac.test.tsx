// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import AlmanacPage, { loader } from "./almanac";
import { almanacSelection, localMidnight } from "../lib/almanac";
import { parseAlmanac } from "../lib/weather-api";
import registry from "../fixtures/metric-registry.json";
import record from "../fixtures/almanac-record.json";
import rain from "../fixtures/almanac-rain_day.json";
import rose from "../fixtures/almanac-wind_rose_bin.json";
import astronomy from "../fixtures/almanac-astronomy.json";
import comparison from "../fixtures/almanac-comparison.json";
import empty from "../fixtures/almanac-empty.json";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
const NOW = Date.parse("2026-11-01T18:00:00Z");

test("local dates become America/Denver midnights across both DST changes", () => {
  expect(localMidnight("2026-03-08")).toBe("2026-03-08T07:00:00Z");
  expect(localMidnight("2026-03-09")).toBe("2026-03-09T06:00:00Z");
  expect(localMidnight("2026-11-01")).toBe("2026-11-01T06:00:00Z");
  expect(localMidnight("2026-11-02")).toBe("2026-11-02T07:00:00Z");
});

test("visitor parameters become one explicit, bounded almanac request", () => {
  const { params } = almanacSelection(new URL("https://weather.example/weather/almanac/?product=comparison&from=2026-03-08&to=2026-03-08&compare=high"), NOW);
  expect(Object.fromEntries(params)).toEqual({
    product: "comparison", start: "2026-03-08T07:00:00Z", end: "2026-03-09T06:00:00Z", metric: "temperature", statistic: "max",
    comparison_start: "2026-03-07T07:00:00Z", comparison_end: "2026-03-08T07:00:00Z",
  });
  const records = almanacSelection(new URL("https://weather.example/weather/almanac/"), NOW).params;
  expect([records.get("product"), records.get("metric"), records.get("statistic"), records.get("start"), records.get("end")]).toEqual(["record", "temperature", "max", "2026-08-04T06:00:00Z", "2026-11-02T07:00:00Z"]);
  expect(records.has("comparison_start")).toBe(false);
  for (const query of ["product=normals", "record=hottest", "from=2026-02-30", "from=2025-01-01&to=2026-01-02", "from=2026-03-09&to=2026-03-08"]) {
    expect(() => almanacSelection(new URL(`https://weather.example/weather/almanac/?${query}`), NOW), query).toThrow(Response);
  }
});

test("the loader issues the almanac and registry reads concurrently", async () => {
  vi.stubEnv("API_ORIGIN", "http://api");
  const pending: Array<[URL, (response: Response) => void]> = [];
  vi.stubGlobal("fetch", vi.fn((url: URL) => new Promise<Response>((resolve) => pending.push([url, resolve]))));
  const result = loader({ request: new Request("https://weather.example/weather/almanac/?product=rain_day"), params: {}, context: {} } as Parameters<typeof loader>[0]);
  await vi.waitFor(() => expect(pending.map(([url]) => url.pathname).sort()).toEqual(["/api/v1/weather/almanac", "/api/v1/weather/metrics"]));
  for (const [url, resolve] of pending) resolve(new Response(JSON.stringify(url.pathname.endsWith("metrics") ? registry : rain)));
  const data = await result;
  expect(data.almanac?.items).toHaveLength(31);
});

test("fixtures parse leniently: unknown fields, items and reasons are dropped", () => {
  for (const fixture of [record, rain, rose, astronomy, comparison, empty]) expect(parseAlmanac(fixture)).not.toBeNull();
  const extended = { ...rain, private_note: "synthetic", missing_reasons: ["partial_coverage", "future_reason"], items: [...rain.items, { product: "future_product" }] };
  const parsed = parseAlmanac(extended);
  expect(JSON.stringify(parsed)).not.toContain("synthetic");
  expect(parsed?.missing_reasons).toEqual(["partial_coverage"]);
  expect(parsed?.items).toHaveLength(rain.items.length);
});

async function page(search: string, fixture: object) {
  vi.stubEnv("API_ORIGIN", "http://api");
  vi.stubGlobal("fetch", vi.fn(async (url: URL) => new Response(JSON.stringify(url.pathname.endsWith("metrics") ? registry : fixture))));
  const router = createMemoryRouter([{ path: "/weather/almanac/", loader, Component: AlmanacPage }], { initialEntries: [`/weather/almanac/${search}`] });
  await act(async () => { render(<RouterProvider router={router} />); });
  await screen.findByRole("heading", { level: 1, name: "Local almanac" });
  return router;
}

test("records rank only qualifying days and say so; empty history is explicit", async () => {
  let router = await page("?units=metric", record);
  expect(screen.getByText(/Station history since/)).toBeTruthy();
  const ranked = screen.getByRole("region", { name: "Ranked days" });
  expect(ranked.textContent).not.toContain("31.5");
  expect(screen.getByRole("region", { name: "Every day" }).textContent).toContain("Below 95% coverage, not ranked");
  router.dispose(); cleanup();
  router = await page("", empty);
  expect(screen.getByText(/No station history yet\. Observed products/)).toBeTruthy();
  expect(screen.getByText("No day in these dates has enough coverage to rank.")).toBeTruthy();
  router.dispose();
});

test("the rose and calendar have table alternatives with whole-interval shares", async () => {
  let router = await page("?product=wind_rose_bin&units=metric", rose);
  const table = screen.getByRole("region", { name: "Wind rose table" });
  expect(table.querySelectorAll("tbody tr")).toHaveLength(19);
  expect(screen.getByRole("img", { name: /Wind rose/ })).toBeTruthy();
  expect(screen.getByText(/never renormalized/)).toBeTruthy();
  router.dispose(); cleanup();
  router = await page("?product=rain_day", rain);
  expect(screen.getByRole("region", { name: "Rain by local day" }).querySelectorAll("tbody tr")).toHaveLength(31);
  expect(document.querySelector(".rain-calendar")?.getAttribute("aria-hidden")).toBe("true");
  router.dispose(); cleanup();
  router = await page("?product=comparison&units=us", comparison);
  expect(screen.getByText(/No difference is shown/)).toBeTruthy();
  router.dispose(); cleanup();
  router = await page("?product=astronomy", astronomy);
  expect(screen.getByRole("region", { name: "Sun and moon times" }).querySelectorAll("tbody tr")).toHaveLength(3);
  router.dispose();
});
