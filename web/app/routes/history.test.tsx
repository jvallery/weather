// SPDX-License-Identifier: MIT
import { afterEach, expect, test, vi } from "vitest";
import { loader, resolutionFor } from "./history";
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
const DAY = 86_400_000;

test("windows longer than 31 days use one point per local day", () => {
  expect(resolutionFor(DAY)).toBe("300");
  expect(resolutionFor(31 * DAY)).toBe("10800");
  expect(resolutionFor(90 * DAY)).toBe("day");
  expect(resolutionFor(365 * DAY)).toBe("day");
});

async function run(query: string) {
  vi.stubEnv("API_ORIGIN", "http://api");
  const fetch = vi.fn(async () => new Response(JSON.stringify({ code: "unavailable" }), { status: 503 }));
  vi.stubGlobal("fetch", fetch);
  return { fetch, result: loader({ request: new Request(`https://weather.example/weather/history/?${query}`), params: {}, context: {} } as unknown as Parameters<typeof loader>[0]) };
}

test("a local date range spans whole America/Denver days, inclusive, across DST", async () => {
  const { fetch, result } = await run("from=2025-11-01&to=2026-10-31&metric=temperature");
  const data = await result;
  // Nov 1 2025 starts at MDT midnight (06:00Z); the day after Oct 31 2026 starts at MDT midnight too.
  expect([data.start, data.end]).toEqual(["2025-11-01T06:00:00.000Z", "2026-11-01T06:00:00.000Z"]);
  expect(data.resolution).toBe("day");
  expect([data.from, data.to]).toEqual(["2025-11-01", "2026-10-31"]);
  // A year is past the raw export's 31-day page budget: no export link.
  expect(data.exportUrl).toBeNull();
  const aggregate = fetch.mock.calls.map((call) => String(call[0])).find((url) => url.includes("/aggregates"));
  expect(aggregate).toContain("resolution=day");
});

test("more than a year, or a reversed or malformed range, is rejected", async () => {
  for (const query of ["from=2025-01-01&to=2026-06-01", "from=2026-06-02&to=2026-06-01", "from=2026-06-01", "from=junk&to=2026-06-01"]) {
    const { result } = await run(query);
    await expect(result).rejects.toBeInstanceOf(Response);
  }
});
