// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import SearchPage from "./search";
import fixture from "../fixtures/search.json";
import registry from "../fixtures/metric-registry.json";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

const SEARCH = "/search/?q=rain&from=2026-10-25&to=2026-10-31&units=us";
type Call = { body: Record<string, unknown>; signal: AbortSignal; resolve: (response: Response) => void };
/** Stubbed fetch: search POSTs wait for the test to answer them; the registry answers at once. */
function stubFetch() {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    if (url === "/api/v1/weather/metrics") return Promise.resolve(new Response(JSON.stringify(registry)));
    expect([url, init?.method, init?.cache]).toEqual(["/api/v1/search", "POST", "no-store"]);
    return new Promise<Response>((resolve, reject) => {
      init!.signal!.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      calls.push({ body: JSON.parse(String(init!.body)), signal: init!.signal!, resolve });
    });
  }));
  return calls;
}
const ok = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
const problem = (status: number, code: string, headers: Record<string, string> = {}) => new Response(JSON.stringify({ code, message: "m" }), { status, headers: { "Content-Type": "application/problem+json", ...headers } });
async function mount(path: string) {
  const router = createMemoryRouter([{ path: "/search/", Component: SearchPage }], { initialEntries: [path] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => { render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>); });
  return router;
}
async function answer(call: Call, response: Response) { await act(async () => { call.resolve(response); }); }

test("a bare page and typing send nothing; one submit sends exactly one typed request and writes the URL", async () => {
  const calls = stubFetch();
  const router = await mount("/search/?units=metric");
  expect(calls).toHaveLength(0);
  fireEvent.change(screen.getByLabelText(/Words to find/), { target: { value: "hail" } });
  fireEvent.click(screen.getByRole("button", { name: "Add measured filter" }));
  fireEvent.change(screen.getByLabelText("Value"), { target: { value: "20" } });
  fireEvent.change(screen.getByLabelText("Measurement"), { target: { value: "wind_gust.max" } });
  expect(calls).toHaveLength(0);
  // A new metric row in metric display units starts in canonical m/s.
  expect((screen.getByLabelText("Unit") as HTMLSelectElement).value).toBe("m/s");
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Search" })); });
  expect(calls).toHaveLength(1);
  const search = new URLSearchParams(router.state.location.search);
  expect(search.get("q")).toBe("hail");
  expect(search.getAll("p")).toEqual(["wind_gust.max:ge:20:m/s:0.95"]);
  expect(search.get("units")).toBe("metric");
  expect(Object.keys(calls[0].body).sort()).toEqual(["cameras", "end", "predicates", "query", "segments", "start", "top_k"]);
  expect(calls[0].body).toMatchObject({ query: "hail", cameras: [], segments: [], top_k: 10, predicates: [{ metric: "wind_gust", statistic: "max", op: ">=", value: 20, unit: "m/s", min_coverage: 0.95 }] });
  expect(String(calls[0].body.start)).toMatch(/^\d{4}-\d\d-\d\dT0[67]:00:00Z$/);
  router.dispose();
});

test("a newer search aborts the older request and a late older response never renders", async () => {
  const calls = stubFetch();
  const router = await mount(SEARCH.replace("q=rain", "q=first"));
  expect(calls).toHaveLength(1);
  await act(async () => { await router.navigate(SEARCH.replace("q=rain", "q=second")); });
  expect(calls).toHaveLength(2);
  expect(calls[0].signal.aborted).toBe(true);
  await answer(calls[1], ok(fixture));
  expect(screen.getByText(/^Results for “second”/)).toBeTruthy();
  // Even if the aborted request's response still arrived, it is ignored.
  await answer(calls[0], ok({ ...fixture, hits: [] }));
  expect(screen.getByText(/^Results for “second”/)).toBeTruthy();
  expect(screen.queryByText(/“first”/)).toBeNull();
  expect(document.querySelectorAll(".search-result")).toHaveLength(3);
  router.dispose();
});

test("each mode and failure renders its own state; no result differs from search unavailable", async () => {
  const cases: [Response, RegExp][] = [
    [ok(fixture), /Ranked by meaning and words: hybrid exact search · profile embed-main-2560-v1/],
    [ok({ ...fixture, mode: "lexical", degraded_reason: "embedding_unavailable", profile_id: null }), /AI search is offline\. These are word matches only/],
    [ok({ ...fixture, mode: "lexical", degraded_reason: "embedding_busy", profile_id: null }), /AI search is busy\. These are word matches only/],
    [ok({ ...fixture, mode: "lexical", degraded_reason: "lexical_no_terms", profile_id: null, count: 0, hits: [] }), /no searchable words .* \(lexical: no terms\)/],
    [ok({ ...fixture, mode: "numeric", profile_id: null }), /No search text: recordings that match the filters, newest first/],
    [ok({ ...fixture, count: 0, hits: [] }), /No recordings match this search/],
    [problem(429, "busy", { "Retry-After": "7" }), /Too many searches are running\. Try again in 7 seconds; nothing is retried automatically/],
    [problem(503, "unavailable"), /This is not an empty result/],
    [problem(400, "range_too_large"), /Measured filters cover at most 31 days per search/],
    [new Response("not json", { status: 200 }), /This is not an empty result/],
  ];
  for (const [response, expected] of cases) {
    const calls = stubFetch();
    const router = await mount(SEARCH);
    await answer(calls[0], response);
    expect(screen.getByText(expected)).toBeTruthy();
    if (!expected.source.includes("No recordings match")) expect(screen.queryByText(/No recordings match/)).toBeNull();
    router.dispose(); cleanup(); vi.unstubAllGlobals();
  }
});

test("busy and unavailable never retry by themselves; Search again sends one request", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const calls = stubFetch();
  const router = await mount(SEARCH);
  await answer(calls[0], problem(503, "unavailable"));
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(calls).toHaveLength(1);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Search again" })); });
  expect(calls).toHaveLength(2);
  router.dispose();
});

test("result cards: evidence, whole-video measurements, unknown rain and validated seeks", async () => {
  const calls = stubFetch();
  const router = await mount(SEARCH);
  await answer(calls[0], ok(fixture));
  const cards = [...document.querySelectorAll<HTMLElement>(".search-result")];
  expect(cards.map((card) => card.querySelector("h3")!.textContent)).toEqual(["East sky · Daytime · solar cycle 2026-10-31", "West sky · Daytime · solar cycle 2026-10-31", "Front yard · Overnight · solar cycle 2026-10-31"]);
  const [east, west, front] = cards;
  // Two hits from one video, one card; each window states its precision and seeks inside it.
  expect(within(east).getByText("Matched windows (2)")).toBeTruthy();
  expect(within(east).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual(["/timelapses/skycam_east/2026-10-31/daytime/?t=2026-10-31T15%3A00%3A00Z", "/timelapses/skycam_east/2026-10-31/daytime/?t=2026-10-31T12%3A30%3A00Z"]);
  expect(east.textContent).toMatch(/Time window · .*Whole recording · /);
  expect(within(east).getByText("Measured · applies to the whole video")).toBeTruthy();
  await vi.waitFor(() => expect(east.textContent).toContain("Maximum gust: 28 mph"));
  expect(east.textContent).toMatch(/Rain total: 0\.05 in · over .* · coverage 99\.0% of 12 h 30 min · 741 observations · evidence hash 1f3a0+/);
  expect(east.textContent).toContain("Mean temperature: 47.1 °F");
  expect(east.textContent).toContain("analysis mode video");
  expect(east.textContent).toContain("media revision 1");
  expect(east.textContent).not.toMatch(/confidence|\bscore\b/i);
  expect(within(east).getByRole("img").getAttribute("src")).toBe("/media/239ed53c-212a-53ef-99a8-0904671e4630");
  // A seek outside the cited window: evidence stays, the location is unavailable, no link.
  expect(within(west).queryAllByRole("link")).toHaveLength(0);
  expect(west.textContent).toContain("Location unavailable: the seek falls outside this window or recording");
  expect(west.textContent).toContain("Time window · ");
  // Under-covered zero rain is unknown, never "0 in" or "no rain".
  expect(west.textContent).toContain("Rain total: Unknown");
  expect(west.textContent).not.toMatch(/\b0 in\b|0 mm|no rain/i);
  expect(within(front).queryAllByRole("link")).toHaveLength(0);
  expect(front.textContent).toContain("Location unavailable: no playable position");
  expect(within(front).queryByRole("img")).toBeNull();
  router.dispose();
});

test("an invalid URL shows every problem and sends nothing; the filter help explains coverage", async () => {
  const calls = stubFetch();
  const router = await mount(`/search/?q=${"a".repeat(513)}&from=2026-10-25&to=2026-10-31&top_k=21&p=precipitation.sum:ge:1:mph:0.95&units=us`);
  const alert = screen.getByRole("alert");
  expect(within(alert).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
    "Search text is 513 characters; the limit is 512.", "Measured filter 1: “mph” is not a unit of rain total.", "Results per search must be a whole number from 1 to 20.",
  ]);
  expect(calls).toHaveLength(0);
  // The invalid unit is kept visible for correction rather than replaced.
  expect((screen.getByLabelText("Unit") as HTMLSelectElement).value).toBe("mph");
  expect(screen.getByText(/never satisfy a filter, even “= 0”: unknown rain is not “no rain”/)).toBeTruthy();
  router.dispose();
});

test("the form keeps its own state across a submit and is restored from the URL on back and forward", async () => {
  stubFetch();
  const router = await mount(SEARCH);
  const button = screen.getByRole("button", { name: "Search" });
  fireEvent.change(screen.getByLabelText(/Words to find/), { target: { value: "hail" } });
  await act(async () => { fireEvent.click(button); });
  expect(new URLSearchParams(router.state.location.search).get("q")).toBe("hail");
  // Same nodes: keyboard focus is not lost to a remount.
  expect(screen.getByRole("button", { name: "Search" })).toBe(button);
  await act(async () => { await router.navigate(-1); });
  expect((screen.getByLabelText(/Words to find/) as HTMLInputElement).value).toBe("rain");
  await act(async () => { await router.navigate(1); });
  expect((screen.getByLabelText(/Words to find/) as HTMLInputElement).value).toBe("hail");
  router.dispose();
});
