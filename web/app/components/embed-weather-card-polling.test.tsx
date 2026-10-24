// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import fixture from "../fixtures/current.json";
import registryFixture from "../fixtures/metric-registry.json";
import { currentSchema, metricRegistrySchema } from "../lib/contracts";
import { EmbedWeatherCard } from "./embed-weather-card";

let client: QueryClient | undefined;
afterEach(() => {
  cleanup(); client?.clear(); vi.useRealTimers(); vi.unstubAllGlobals();
  vi.restoreAllMocks(); focusManager.setFocused(undefined);
});

test("one-second source-age renders do not postpone the jittered 15-second refresh", async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date(fixture.generated_at));
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  focusManager.setFocused(true);
  let calls = 0;
  vi.spyOn(Math, "random").mockImplementation(() => (++calls % 2 ? 0.2 : 0.8));
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(fixture))));
  vi.stubGlobal("fetch", fetch);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><EmbedWeatherCard initial={currentSchema.parse(fixture)}
    renderedAt={Date.now()} registry={metricRegistrySchema.parse(registryFixture)} /></QueryClientProvider>);
  // Advance separately so React renders each age tick as it does in the browser.
  for (let tick = 0; tick < 17; tick += 1) {
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  }
  expect(fetch).toHaveBeenCalledTimes(1);
});
