// SPDX-License-Identifier: MIT
import { readFileSync } from "node:fs";
import { afterEach, expect, test, vi } from "vitest";
import { loader } from "./stills";
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
const fixture = readFileSync(new URL("../fixtures/stills.json", import.meta.url), "utf8");
const call = (url: string) => loader({ request: new Request(url), params: {}, context: {} } as unknown as Parameters<typeof loader>[0]);

test("a day view asks for the calendar day; a clock hour compares the week ending on it", async () => {
  vi.stubEnv("API_ORIGIN", "http://api");
  const fetch = vi.fn().mockImplementation(async () => new Response(fixture));
  vi.stubGlobal("fetch", fetch);
  const day = await call("https://weather.example/stills/?camera=skycam_west&date=2026-11-01");
  expect(Object.fromEntries((fetch.mock.calls[0][0] as URL).searchParams)).toEqual({ camera: "skycam_west", date: "2026-11-01" });
  // Images of an unknown type are dropped; the repeated 01:00 keeps its own offset and fold.
  expect(day.page?.items.map((still) => [still.local_label, still.fold, still.state])).toEqual([
    ["2026-10-30T01:00:00-06:00", 0, "missing"], ["2026-10-31T01:00:00-06:00", 0, "on_target"],
    ["2026-11-01T01:00:00-06:00", 0, "on_target"], ["2026-11-01T01:00:00-07:00", 1, "off_target"], ["2026-11-01T02:00:00-07:00", 0, "missing"],
  ]);
  await call("https://weather.example/stills/?camera=skycam_east&date=2026-11-01&hour=1");
  expect(Object.fromEntries((fetch.mock.calls[1][0] as URL).searchParams)).toEqual({ camera: "skycam_east", from: "2026-10-26", to: "2026-11-01", hour: "1" });
  for (const bad of ["camera=unknown", "date=2026-02-30", "hour=24"]) await expect(call(`https://weather.example/stills/?${bad}`)).rejects.toBeInstanceOf(Response);
});

test("an unknown state degrades to pending and an image outside /media/ is dropped", async () => {
  vi.stubEnv("API_ORIGIN", "http://api");
  const value = JSON.parse(fixture);
  value.items[0].state = "future_state";
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(value))));
  expect((await call("https://weather.example/stills/?date=2026-11-01")).page?.items[0].state).toBe("pending");
  value.items[1].images[0].url = "https://elsewhere.example/original.jpg";
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(value))));
  const images = (await call("https://weather.example/stills/?date=2026-11-01")).page?.items[1].images;
  expect(images?.map((image) => image.url).every((url) => url.startsWith("/media/"))).toBe(true);
});
