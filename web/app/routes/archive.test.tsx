// SPDX-License-Identifier: MIT
import { afterEach, expect, test, vi } from "vitest";
import { loader } from "./archive";
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

// The shape /api/v1/timelapses serves (#18); fields the archive does not render are ignored.
const entry = (video_id: string, cycle_date: string, state: string, reason: string | null) => ({
  schema_version: 1, video_id, camera: "skycam_east", cycle_date, segment: "daytime", state, reason,
  withdrawn: state === "withdrawn", revision: 1, updated_at: "2026-10-02T02:00:00Z",
  source_interval: { start: "2026-10-01T13:00:00Z", end: "2026-10-02T00:30:00Z" }, allocated_frames: 4200,
  renditions: [], poster: null, sidecar: null, native_download_url: null, keogram_state: "pending", keogram: null,
});

test("the archive month page reads its month (both API pages) and lists that month's videos", async () => {
  vi.stubEnv("API_ORIGIN", "http://api");
  const page = (next: string | null, items: unknown[]) => new Response(JSON.stringify({ generated_at: "2026-10-24T00:00:00Z", next_cursor: next, items }));
  const fetch = vi.fn()
    .mockResolvedValueOnce(page("page-2", [entry("00000000-0000-4000-8000-000000000001", "2026-10-31", "partial", "low_coverage")]))
    .mockResolvedValueOnce(page(null, [entry("00000000-0000-4000-8000-000000000002", "2026-10-01", "withdrawn", "withdrawn")]));
  vi.stubGlobal("fetch", fetch);
  const data = await loader({ request: new Request("https://weather.example/archive/2026/10/?camera=skycam_east&cursor=stale"), params: { year: "2026", month: "10" }, context: {} } as unknown as Parameters<typeof loader>[0]);
  const [first, second] = fetch.mock.calls.map((call) => call[0] as URL);
  expect(first.pathname).toBe("/api/v1/timelapses");
  expect(Object.fromEntries(first.searchParams)).toEqual({ camera: "skycam_east", month: "2026-10", limit: "100" });
  // The second page follows the cursor bound to the same filters; a cursor in the URL is ignored.
  expect(Object.fromEntries(second.searchParams)).toEqual({ camera: "skycam_east", month: "2026-10", cursor: "page-2", limit: "100" });
  expect(data.catalog?.items.map((video) => [video.cycle_date, video.state, video.reason])).toEqual([["2026-10-31", "partial", "low_coverage"], ["2026-10-01", "withdrawn", "withdrawn"]]);
  // Month navigation links carry the filters, not the month query parameter.
  expect(data.filters).toEqual({ camera: "skycam_east" });
});

test("the calendar's cover is the 480 px poster when the API provides one", async () => {
  vi.stubEnv("API_ORIGIN", "http://api");
  const small = { asset_id: "a1", class: "poster", url: "/media/00000000-0000-4000-8000-0000000000aa", mime: "image/jpeg", bytes: 30_000, width: 480, height: 218 };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    generated_at: "2026-10-24T00:00:00Z", next_cursor: null,
    items: [{ ...entry("00000000-0000-4000-8000-000000000001", "2026-10-31", "published", null), poster_small: small }, entry("00000000-0000-4000-8000-000000000002", "2026-10-30", "published", null)],
  }))));
  const data = await loader({ request: new Request("https://weather.example/archive/2026/10/"), params: { year: "2026", month: "10" }, context: {} } as unknown as Parameters<typeof loader>[0]);
  expect(data.catalog?.items.map((video) => video.poster_small?.url ?? null)).toEqual([small.url, null]);
});
