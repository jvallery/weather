// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { StrictMode } from "react";
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { currentSchema } from "./contracts";
import currentFixture from "../fixtures/current.json";
import { useCurrent } from "./use-current";
import { nextPollDelay } from "./polling";
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
