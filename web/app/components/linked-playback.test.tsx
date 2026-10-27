// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { LinkedPlayback } from "./linked-playback";
import { PLOT_REQUESTS, plotBins, plotQueries, plotResolution, seriesSummary } from "./linked-plots";
import { CameraTiles, latestMedia } from "./camera-tiles";
import { instant, iso } from "../lib/time-map";
import { parseEvents, parseRegistry, parseTimelapses, type Aggregates } from "../lib/weather-api";
import catalog from "../fixtures/catalog.json";
import eventsFixture from "../fixtures/events.json";
import registryFixture from "../fixtures/metric-registry.json";

// uPlot needs a real canvas; the panels are covered in fleet CI's browsers.
vi.mock("uplot", () => ({ default: class { over = document.createElement("div"); destroy() {} setSize() {} valToPos() { return 0; } posToVal() { return 0; } } }));
const media = parseTimelapses(catalog)!.items;
const video = media.find((item) => item.camera === "skycam_east" && item.cycle_date === "2026-10-31" && item.segment === "daytime")!;
const registry = parseRegistry(registryFixture);
const events = parseEvents(eventsFixture)!.items;
const A = instant(video.source_interval.start)!, B = instant(video.source_interval.end)!;
const span = { start: A, end: B };
// 12.5 s frames; slates for 1,200–1,271 (15 min) and 2,000–2,002.
const sidecar = { schema_version: 1, source_interval: video.source_interval, time_map: { frames: 3600, fps: 60, timescale: 60000, sample_ticks: 1000, terminal_ticks: 3600000, source_step_seconds: { numerator: 25, denominator: 2 } }, coverage: { slots: { fraction: 0.95 }, exposure: { gaps: [[1200, 1272], [2000, 2003]] } } };
/** Aggregates exactly as the API bins them: epoch-aligned, clipped to [start, end); 15:00–15:45Z has no observations. */
function aggregates(query: URLSearchParams): Aggregates {
  const start = Date.parse(query.get("start")!), end = Date.parse(query.get("end")!), step = Number(query.get("resolution")) * 1000;
  const items: Aggregates["items"][number][] = [];
  for (let lo = Math.floor(start / step) * step; lo < end; lo += step) {
    const a = Math.max(lo, start), b = Math.min(lo + step, end), gap = a >= Date.parse("2026-10-31T15:00:00Z") && a < Date.parse("2026-10-31T15:45:00Z");
    for (const metric of query.getAll("metrics")) for (const statistic of query.getAll("statistics")) items.push({ metric, statistic, start: new Date(a).toISOString(), end: new Date(b).toISOString(), value: gap ? null : 10 + (a - start) / 3_600_000, unit: metric === "temperature" ? "degC" : metric === "humidity" ? "%" : metric === "station_pressure" ? "hPa" : metric === "precipitation" ? "mm" : "m/s", coverage: { fraction: gap ? 0 : 1, support_seconds: gap ? 0 : (b - a) / 1000, expected_seconds: (b - a) / 1000 }, evidence_count: gap ? 0 : 5, evidence_hash: null, reason: gap ? "No valid observations in this interval." : null, direction_state: null, rose: null });
  }
  return { generated_at: query.get("start")!, start: query.get("start")!, end: query.get("end")!, resolution: query.get("resolution")!, items };
}
let requests: string[] = [];
beforeEach(() => {
  requests = [];
  vi.spyOn(HTMLMediaElement.prototype, "readyState", "get").mockReturnValue(1);
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    requests.push(url);
    if (url === video.sidecar!.url) return new Response(JSON.stringify(sidecar));
    if (url.startsWith("/api/v1/weather/aggregates?")) return new Response(JSON.stringify(aggregates(new URLSearchParams(url.split("?")[1]))));
    return new Response("{}", { status: 404 });
  }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function mount(requested: string | null = null) {
  const router = createMemoryRouter([{ path: "/", element: <LinkedPlayback video={video} requested={requested} events={events.filter((event) => event.camera === null || event.camera === "skycam_east")} units="metric" registry={registry} /> }]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  const player = view.container.querySelector("video")!;
  Object.defineProperty(player, "duration", { configurable: true, value: 60 });
  await screen.findByText(/ of 3,600 · /);
  await screen.findByText(/Temperature mean: /);
  const readout = () => document.getElementById("cursor-readout")!;
  const seekForm = async (value: string) => {
    fireEvent.change(screen.getByLabelText(/Seek to source time/), { target: { value } });
    await act(async () => { fireEvent.submit(screen.getByRole("button", { name: "Seek" })); });
  };
  return { player, readout, seekForm, router, client };
}

test("plot requests cover exactly the segment interval within the point ceiling", () => {
  const queries = plotQueries(span);
  expect(queries.map((query) => [query.get("start"), query.get("end")])).toEqual(Array(3).fill(["2026-10-31T12:30:00Z", "2026-11-01T01:00:00Z"]));
  expect(queries[0].getAll("metrics")).toEqual(["temperature", "humidity", "station_pressure", "wind_avg"]);
  // The longest possible segment (24 h) stays far below the API's 5,000-point ceiling, mobile included.
  for (const hours of [12.5, 17, 24]) {
    const long = { start: A, end: A + BigInt(hours * 3600) * 1_000_000n }, resolution = plotResolution(long);
    const points = PLOT_REQUESTS.reduce((sum, request) => sum + (Math.ceil((hours * 3600) / resolution) + 1) * request.metrics.length * request.statistics.length, 0);
    expect(points).toBeLessThan(2000);
  }
  expect(plotResolution(span)).toBe(300);
});
test("telemetry gaps stay null and the summary counts them; values outside the interval are ignored", () => {
  const responses = plotQueries(span).map(aggregates);
  (responses[0].items as Aggregates["items"][number][]).push({ ...responses[0].items[0], start: "2026-10-31T12:00:00Z", end: "2026-10-31T12:05:00Z" });
  const bins = plotBins(span, responses);
  expect(bins).toHaveLength(150); expect(bins[0].start).toBe(A); expect(bins.at(-1)!.end).toBe(B);
  expect(bins.filter((bin) => bin.values["temperature:mean"] === null)).toHaveLength(9);
  expect(seriesSummary(bins, "temperature", "mean", "metric", registry)).toMatch(/141 of 150 intervals have values, 9 without observations; coverage 94\.0%/);
});
test("a deep link seeks once the sidecar map loads; playback moves the cursor only when the frame changes", async () => {
  const { player, readout } = await mount("2026-10-31T15:20:00Z");
  await waitFor(() => expect(readout().dataset.utc).toBe("2026-10-31T15:20:00Z"));
  expect(readout().dataset.frame).toBe("816");
  expect(player.currentTime).toBeCloseTo(816.5 / 60, 9);
  // The seek's own echo is the same frame: the cursor keeps the exact selected instant.
  fireEvent.seeked(player); fireEvent.timeUpdate(player);
  expect(readout().dataset.utc).toBe("2026-10-31T15:20:00Z");
  // Playback to frame 1,200 (inside a capture gap) moves the cursor to that frame's centre.
  player.currentTime = 20.0;
  fireEvent.timeUpdate(player);
  expect(readout().dataset.frame).toBe("1200");
  expect(readout().dataset.utc).toBe(iso(A + 15_006_250_000n));
  expect(readout().textContent).toMatch(/Capture gap: this frame is a slate/);
  expect(requests.filter((url) => url.startsWith("/api/v1/weather/aggregates?")).every((url) => url.includes("start=2026-10-31T12%3A30%3A00Z&end=2026-11-01T01%3A00%3A00Z"))).toBe(true);
});
test("time input, keyboard, events and rejection all seek through the one map", async () => {
  const { player, readout, seekForm } = await mount();
  expect(readout().dataset.utc).toBe("2026-10-31T12:30:06.25Z");
  await seekForm("2026-10-31T15:10:00Z");
  expect(readout().dataset.frame).toBe("768"); expect(player.currentTime).toBeCloseTo(768.5 / 60, 9);
  // Inside the telemetry gap: no value is interpolated.
  expect(readout().textContent).toMatch(/Temperature mean no observations/);
  expect(window.location.search).toBe("?t=2026-10-31T15%3A10%3A00Z");
  // The exclusive end and anything before the start are rejected, and the cursor stays.
  await seekForm(video.source_interval.end);
  expect(screen.getByRole("status").textContent).toMatch(/outside this recording's source interval; the seek was rejected/);
  expect(readout().dataset.frame).toBe("768");
  await seekForm("not a time");
  expect(screen.getByRole("status").textContent).toMatch(/Enter a UTC time/);
  // Keyboard: one plot interval (300 s) per arrow key, to that interval's centre.
  const plots = screen.getByRole("slider", { name: /Measured weather plots/ });
  fireEvent.keyDown(plots, { key: "ArrowRight" });
  expect(readout().dataset.utc).toBe("2026-10-31T15:17:30Z");
  fireEvent.keyDown(plots, { key: "Home" });
  expect(readout().dataset.utc).toBe("2026-10-31T12:32:30Z");
  expect(plots.getAttribute("aria-valuetext")).toMatch(/±150 s/);
  // An event seeks to the start of its overlap with this recording.
  await act(async () => { fireEvent.click(screen.getByRole("link", { name: /^Seek video to precipitation onset/ })); });
  expect(readout().dataset.utc).toBe("2026-10-31T15:20:00Z"); expect(player.currentTime).toBeCloseTo(816.5 / 60, 9);
  // Keograms: one of 1,440 equal UTC bins (31.25 s) per arrow key.
  const keogram = screen.getByRole("slider", { name: /Keogram bin/ });
  fireEvent.keyDown(keogram, { key: "ArrowRight" });
  expect(readout().dataset.utc).toBe("2026-10-31T15:20:34.375Z");
  expect(keogram.getAttribute("aria-valuetext")).toMatch(/^Bin 328 of 1440.*±15\.6 s$/);
  expect(screen.getByText(/Station measurements · shared by all cameras/)).toBeTruthy();
});
test("a sidecar from another revision disables linking without breaking playback", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(url === video.sidecar!.url ? { ...sidecar, time_map: { ...sidecar.time_map, frames: 3599, terminal_ticks: 3599000 } } : aggregates(new URLSearchParams(url.split("?")[1]))))));
  const router = createMemoryRouter([{ path: "/", element: <LinkedPlayback video={{ ...video, keogram_state: "pending", keogram: null }} requested={null} events={[]} units="us" registry={registry} /> }]);
  const view = render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>);
  expect(await screen.findByText(/source-time map is unavailable or does not match this recording/)).toBeTruthy();
  expect(view.container.querySelectorAll("video")).toHaveLength(1);
  expect(screen.getByText(/^Keogram pending · the recording still plays/)).toBeTruthy();
});
test("Today tiles show the latest poster, keogram and state per camera and segment, and say what is missing", () => {
  const latest = latestMedia(media);
  expect(latest.get("skycam_east/overnight")!.cycle_date).toBe("2026-10-31");
  const router = createMemoryRouter([{ path: "/", element: <CameraTiles media={media.filter((item) => item.camera !== "frontyard_1" || item.segment !== "overnight")} /> }]);
  render(<RouterProvider router={router} />);
  expect(screen.getAllByRole("link", { name: /^Poster frame, .*Watch/ }).length).toBe(5);
  expect(screen.getByRole("link", { name: /^Keogram, East sky · daytime · 2026-10-31$/ }).getAttribute("href")).toBe("/timelapses/skycam_east/2026-10-31/daytime/");
  expect(screen.getAllByText("Keogram pending.").length).toBeGreaterThan(0);
  expect(screen.getByText("Keogram unavailable.")).toBeTruthy();
  expect(screen.getAllByText(/No recording published yet/)).toHaveLength(1);
  expect(screen.queryByText(/latest video · coming soon/)).toBeNull();
  cleanup();
  render(<RouterProvider router={createMemoryRouter([{ path: "/", element: <CameraTiles media={null} /> }])} />);
  expect(screen.getByText(/^Recording API unavailable/)).toBeTruthy();
});
