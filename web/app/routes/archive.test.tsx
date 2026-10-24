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

test("the archive month page asks the API for its month and lists that month's videos", async () => {
  vi.stubEnv("API_ORIGIN", "http://api");
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    generated_at: "2026-10-24T00:00:00Z", next_cursor: "page-2",
    items: [entry("00000000-0000-4000-8000-000000000001", "2026-10-31", "partial", "low_coverage"), entry("00000000-0000-4000-8000-000000000002", "2026-10-01", "withdrawn", "withdrawn")],
  })));
  vi.stubGlobal("fetch", fetch);
  const data = await loader({ request: new Request("https://weather.example/archive/2026/10/?camera=skycam_east"), params: { year: "2026", month: "10" }, context: {} } as unknown as Parameters<typeof loader>[0]);
  const target = fetch.mock.calls[0][0] as URL;
  expect(target.pathname).toBe("/api/v1/timelapses");
  expect(Object.fromEntries(target.searchParams)).toEqual({ camera: "skycam_east", month: "2026-10", limit: "100" });
  expect(data.catalog?.items.map((video) => [video.cycle_date, video.state, video.reason])).toEqual([["2026-10-31", "partial", "low_coverage"], ["2026-10-01", "withdrawn", "withdrawn"]]);
  expect(data.catalog?.next_cursor).toBe("page-2");
  // Month navigation links carry the filters, not the month query parameter.
  expect(data.filters).toEqual({ camera: "skycam_east" });
});
