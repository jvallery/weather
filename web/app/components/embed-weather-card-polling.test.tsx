// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { StrictMode } from "react";
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import fixture from "../fixtures/current.json";
import registryFixture from "../fixtures/metric-registry.json";
import { parseCurrent, parseRegistry } from "../lib/weather-api";
import { EmbedWeatherCard } from "./embed-weather-card";

const current = parseCurrent(fixture)!;
/** The API's answer at the moment it is asked: temperature observed 5 s earlier. */
function live() {
  const now = Date.now();
  return new Response(JSON.stringify({ ...fixture, generated_at: new Date(now).toISOString(), metrics: [{ ...fixture.metrics[0],
    observed_at: new Date(now - 5000).toISOString(), received_at: new Date(now - 4000).toISOString(), valid_until: new Date(now + 85_000).toISOString() }] }));
}
let client: QueryClient;
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date(fixture.generated_at));
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  focusManager.setFocused(true);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => {
  cleanup(); client.clear(); vi.useRealTimers(); vi.unstubAllGlobals();
  vi.restoreAllMocks(); focusManager.setFocused(undefined);
});
function mount() {
  render(<StrictMode><QueryClientProvider client={client}><EmbedWeatherCard initial={current}
    renderedAt={Date.now()} registry={parseRegistry(registryFixture)} /></QueryClientProvider></StrictMode>);
}
// Advance in one-second steps so React renders each age tick as it does in the browser.
async function advance(milliseconds: number) {
  for (let left = milliseconds; left > 0; left -= 1000) await act(async () => { await vi.advanceTimersByTimeAsync(Math.min(1000, left)); });
}
async function visibility(state: "hidden" | "visible", events = 1) {
  await act(async () => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: state });
    for (let event = 0; event < events; event += 1) document.dispatchEvent(new Event("visibilitychange"));
  });
}
/** Records the most requests ever open at once; an aborted request counts as closed. */
function trackedFetch(respond: () => Promise<Response>) {
  const state = { open: 0, most: 0 };
  const fetch = vi.fn((_url: string, init: RequestInit) => {
    state.open += 1; state.most = Math.max(state.most, state.open);
    let closed = false;
    const close = () => { if (!closed) { closed = true; state.open -= 1; } };
    init.signal?.addEventListener("abort", close, { once: true });
    return respond().finally(close);
  });
  vi.stubGlobal("fetch", fetch);
  return { fetch, state };
}

test("polls about every 15 s while visible, pauses while hidden and refreshes exactly once on resume", async () => {
  vi.spyOn(Math, "random").mockReturnValue(0.5); // Midpoint: no jitter.
  const { fetch, state } = trackedFetch(async () => live());
  mount();
  await advance(14_000); expect(fetch).not.toHaveBeenCalled();
  await advance(1000); expect(fetch).toHaveBeenCalledTimes(1);
  await advance(15_000); expect(fetch).toHaveBeenCalledTimes(2);
  await visibility("hidden");
  await advance(120_000); expect(fetch).toHaveBeenCalledTimes(2);
  // Browsers can repeat the event; one resume is one refresh.
  await visibility("visible", 3); await advance(1000);
  expect(fetch).toHaveBeenCalledTimes(3);
  await advance(10_000); expect(fetch).toHaveBeenCalledTimes(3);
  expect(state.most).toBe(1);
});

test("one request in flight: a slow poll is never overlapped, even by a resume", async () => {
  vi.spyOn(Math, "random").mockReturnValue(0.5);
  const { fetch, state } = trackedFetch(() => new Promise<Response>(() => {}));
  mount();
  await advance(15_000); expect(fetch).toHaveBeenCalledTimes(1);
  await advance(60_000); expect(fetch).toHaveBeenCalledTimes(1);
  await visibility("visible", 2); expect(fetch).toHaveBeenCalledTimes(1);
  // Hiding cancels the open request; the resume then starts the only new one.
  await visibility("hidden"); await visibility("visible"); await advance(1000);
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(state.most).toBe(1);
});

test("failures back off within the jitter bound while ages advance; recovery shows truthful ages", async () => {
  vi.spyOn(Math, "random").mockReturnValue(1); // Upper bound: +10%.
  let failing = true;
  const { fetch } = trackedFetch(async () => { if (failing) throw new TypeError("Synthetic network outage"); return live(); });
  mount();
  expect(screen.getByText("5 s old")).toBeTruthy();
  await advance(16_000); expect(fetch).not.toHaveBeenCalled();
  await advance(500); expect(fetch).toHaveBeenCalledTimes(1);
  await advance(500);
  expect(screen.getByText(/^API unavailable\./)).toBeTruthy();
  expect(screen.getByText("22 s old")).toBeTruthy();
  // Then 30 s × 1.1 and 60 s × 1.1 after each failure, measured from the failed request.
  await advance(32_000); expect(fetch).toHaveBeenCalledTimes(1);
  await advance(1000); expect(fetch).toHaveBeenCalledTimes(2);
  expect(screen.getByText("55 s old")).toBeTruthy();
  failing = false;
  await advance(65_000); expect(fetch).toHaveBeenCalledTimes(2);
  await advance(1000); expect(fetch).toHaveBeenCalledTimes(3);
  // The recovered value is 5 s old at its own source time, not the age of the outage.
  const observedAt = new Date(Date.now() - 5500).toISOString();
  expect(screen.queryByText(/^API unavailable\./)).toBeNull();
  expect(screen.getByText("5 s old")).toBeTruthy();
  expect(document.querySelector(`time[datetime="${observedAt}"]`)).not.toBeNull();
});

test("one-second source-age renders do not postpone the jittered 15-second refresh", async () => {
  let calls = 0;
  vi.spyOn(Math, "random").mockImplementation(() => (++calls % 2 ? 0.2 : 0.8));
  const { fetch } = trackedFetch(async () => live());
  mount();
  await advance(17_000);
  expect(fetch).toHaveBeenCalledTimes(1);
});
