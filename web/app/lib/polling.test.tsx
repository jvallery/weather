// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { StrictMode, useEffect, useState } from "react";
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { currentSchema } from "./contracts";
import currentFixture from "../fixtures/current.json";
import { useCurrent } from "./use-current";
import { CURRENT_QUERY_KEY, nextPollDelay } from "./polling";
import { CurrentConditions } from "../components/current-conditions";

const current = currentSchema.parse(currentFixture);
function Probe() {
  const result = useCurrent(current);
  return <p data-testid="source">{result.data.metrics[0].observed_at} · {result.isError ? "API unavailable" : "available"}</p>;
}
let client: QueryClient;
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-01T08:15:05Z"));
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  focusManager.setFocused(true);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => { cleanup(); client.clear(); vi.useRealTimers(); vi.unstubAllGlobals(); focusManager.setFocused(undefined); });
function mount() { render(<StrictMode><QueryClientProvider client={client}><Probe /></QueryClientProvider></StrictMode>); }
async function advance(milliseconds: number) { await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); }); }
test("15-second polling is seeded; StrictMode, focus and reconnect cause no duplicates", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(current))); vi.stubGlobal("fetch", fetch);
  mount(); expect(fetch).not.toHaveBeenCalled();
  await advance(14_999); expect(fetch).not.toHaveBeenCalled();
  await advance(1); expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => { window.dispatchEvent(new Event("focus")); window.dispatchEvent(new Event("online")); });
  await advance(1000); expect(fetch).toHaveBeenCalledTimes(1);
});
test("one request stays in flight and hidden pages cancel it and stop polling", async () => {
  let signal: AbortSignal | undefined;
  const fetch = vi.fn((_url, init) => { signal = init.signal; return new Promise<Response>(() => {}); });
  vi.stubGlobal("fetch", fetch); mount(); await advance(15_000); expect(fetch).toHaveBeenCalledTimes(1);
  await advance(20_000); expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" }); document.dispatchEvent(new Event("visibilitychange")); });
  expect(signal?.aborted).toBe(true); await advance(120_000); expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" }); document.dispatchEvent(new Event("visibilitychange")); });
  expect(fetch).toHaveBeenCalledTimes(1);
  await advance(15_000); expect(fetch).toHaveBeenCalledTimes(2);
});
test("failure keeps observed_at and backs off to a bounded cadence", async () => {
  const fetch = vi.fn().mockRejectedValue(new Error("Synthetic network outage")); vi.stubGlobal("fetch", fetch);
  mount(); await advance(15_001); expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.getByTestId("source").textContent).toContain(current.metrics[0].observed_at);
  expect(screen.getByTestId("source").textContent).toContain("API unavailable");
  await advance(29_998); expect(fetch).toHaveBeenCalledTimes(1);
  await advance(1); expect(fetch).toHaveBeenCalledTimes(2);
  expect(client.getQueryData(["weather", "current"])).toEqual(current);
  expect(nextPollDelay(99)).toBe(120_000);
});

test("outage values keep aging without repeating the live-region announcement", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Synthetic outage")));
  render(<QueryClientProvider client={client}><CurrentConditions initial={current} renderedAt={Date.now()} units="us" /></QueryClientProvider>);
  const announcement = screen.getByRole("status");
  const announced = announcement.textContent;
  const mutations = vi.fn(); const observer = new MutationObserver(mutations);
  observer.observe(announcement, { childList: true, characterData: true, subtree: true });
  expect(screen.getByText("5 s old")).toBeTruthy();
  await advance(15_001);
  expect(screen.getByText("20 s old")).toBeTruthy();
  expect(screen.getByText(/API unavailable/)).toBeTruthy();
  expect(screen.getByText(/Unavailable · Synthetic fixture: missing support/)).toBeTruthy();
  expect(screen.getByText(/1 h old · stale/)).toBeTruthy();
  expect(screen.getByText("Synthetic fixture: no recent wind report")).toBeTruthy();
  expect(announcement.textContent).toBe(announced); expect(mutations).not.toHaveBeenCalled();
  expect(client.getQueryData(["weather", "current"])).toEqual(current);
  observer.disconnect();
});

test("authoritative unavailable data clears older cached numbers without claiming an HTTP failure", async () => {
  client.setQueryData(CURRENT_QUERY_KEY, current);
  const unavailable = currentSchema.parse({ ...current, data_state: "unavailable", metrics: current.metrics.map((metric) => ({ ...metric, value: null, freshness: "unavailable", reason: "Source evidence invalidated" })) });
  render(<QueryClientProvider client={client}><CurrentConditions initial={unavailable} renderedAt={Date.now()} units="us" /></QueryClientProvider>);
  await advance(0);
  expect(client.getQueryData(CURRENT_QUERY_KEY)).toEqual(unavailable);
  expect(screen.queryByText(/API unavailable/)).toBeNull();
  expect(screen.getByText(/No qualified current/)).toBeTruthy();
});
test("loader transport failure preserves cached source timestamps until polling succeeds", async () => {
  client.setQueryData(CURRENT_QUERY_KEY, current);
  const unavailable = currentSchema.parse({ ...current, data_state: "unavailable", metrics: [] });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(unavailable))));
  render(<QueryClientProvider client={client}><CurrentConditions initial={unavailable} apiUnavailable renderedAt={Date.now()} units="us" /></QueryClientProvider>);
  expect(client.getQueryData(CURRENT_QUERY_KEY)).toEqual(current);
  expect(screen.getByText(/API unavailable/)).toBeTruthy();
  await advance(15_000);
  expect(client.getQueryData(CURRENT_QUERY_KEY)).toEqual(unavailable);
  expect(screen.queryByText(/API unavailable/)).toBeNull();
});

test("optional embed resume refresh deduplicates visibility events and retains bounded jitter", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(current))); vi.stubGlobal("fetch", fetch);
  vi.spyOn(Math, "random").mockReturnValue(1);
  function EmbedProbe() { useCurrent(current, false, { refreshOnResume: true, jitterFraction: 0.1 }); return null; }
  render(<QueryClientProvider client={client}><EmbedProbe /></QueryClientProvider>);
  await advance(16_499); expect(fetch).not.toHaveBeenCalled();
  await advance(1); expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" }); document.dispatchEvent(new Event("visibilitychange")); });
  await act(async () => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" }); document.dispatchEvent(new Event("visibilitychange")); document.dispatchEvent(new Event("visibilitychange")); });
  expect(fetch).toHaveBeenCalledTimes(2);
  vi.restoreAllMocks();
});

test("a canceled old poll cannot resurrect numbers after authoritative source invalidation", async () => {
  let finish: ((response: Response) => void) | undefined;
  let signal: AbortSignal | undefined;
  vi.stubGlobal("fetch", vi.fn((_url, init) => { signal = init.signal; return new Promise<Response>((resolve) => { finish = resolve; }); }));
  const unavailable = currentSchema.parse({ ...current, data_state: "unavailable", metrics: [] });
  const tree = (initial: typeof current) => <QueryClientProvider client={client}><CurrentConditions initial={initial} renderedAt={Date.now()} units="us" /></QueryClientProvider>;
  const view = render(tree(current));
  await advance(15_000);
  view.rerender(tree(unavailable));
  expect(signal?.aborted).toBe(true);
  await act(async () => { finish?.(new Response(JSON.stringify(current))); });
  expect(client.getQueryData(CURRENT_QUERY_KEY)).toEqual(unavailable);
  expect(screen.getByText(/No current measurements were returned/)).toBeTruthy();
});


test("jitter stays stable while one-second source-age renders continue", async () => {
  let samples = 0;
  const random = vi.spyOn(Math, "random").mockImplementation(() => (++samples % 2 ? 0.2 : 0.8));
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(current))));
  vi.stubGlobal("fetch", fetch);
  function AgingProbe() {
    const result = useCurrent(current, false, { refreshOnResume: true, jitterFraction: 0.1 });
    const [tick, setTick] = useState(0);
    useEffect(() => { const timer = setInterval(() => setTick((value) => value + 1), 1000); return () => clearInterval(timer); }, []);
    return <p>{result.data.metrics[0].observed_at} · {tick}</p>;
  }
  try {
    render(<QueryClientProvider client={client}><AgingProbe /></QueryClientProvider>);
    // Separate acts model actual clock renders rather than batching the whole interval.
    for (let tick = 0; tick < 17; tick += 1) await advance(1000);
    expect(fetch).toHaveBeenCalledTimes(1);
  } finally { random.mockRestore(); }
});
