// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import Today, { loader } from "./today";
import current from "../fixtures/current.json";
import registry from "../fixtures/metric-registry.json";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

test("the Today loader issues its API reads concurrently", async () => {
  vi.stubEnv("API_ORIGIN", "http://api");
  const pending: Array<[string, (response: Response) => void]> = [];
  vi.stubGlobal("fetch", vi.fn((url: URL) => new Promise<Response>((resolve) => pending.push([url.pathname, resolve]))));
  const result = loader({ request: new Request("https://weather.example/"), params: {}, context: {} } as Parameters<typeof loader>[0]);
  await vi.waitFor(() => expect(pending.map(([path]) => path).sort()).toEqual(["/api/v1/weather/current", "/api/v1/weather/metrics"]));
  for (const [path, resolve] of pending) resolve(new Response(JSON.stringify(path.endsWith("current") ? current : registry)));
  const data = await result;
  expect(data.apiUnavailable).toBe(false); expect(data.registry).not.toBeNull();
});

test("before the API serves data, Today says API unavailable and fabricates no values", async () => {
  vi.stubEnv("API_ORIGIN", "http://api");
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
  const router = createMemoryRouter([{ path: "/", loader, Component: Today }]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  try {
    await act(async () => { render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>); });
    expect(await screen.findByText(/^API unavailable\./)).toBeTruthy();
    expect(screen.getByText("No current measurements were returned. No substitute values are displayed.")).toBeTruthy();
    expect(document.querySelectorAll(".metric")).toHaveLength(0);
    expect(screen.queryByText(/No qualified current measurements/)).toBeNull();
  } finally { router.dispose(); client.clear(); }
});
