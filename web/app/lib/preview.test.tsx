// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { PreviewTile } from "../components/camera-previews";
import { captureLabel, previewTile, SERVICE_DOWN } from "./preview";
import { parsePreview, type Preview } from "./weather-api";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

const ID = "0f0e0d0c-0b0a-4908-8706-050403020100";
function pointer(age: number, state = "fresh"): Preview {
  const value = parsePreview({
    schema_version: 1, camera: "skycam_east", generated_at: "2026-10-24T18:12:00Z", state, reason: null,
    refresh_seconds: 60, fresh_seconds: 1200, stale_seconds: 1800, minimum_delay_seconds: 600, skew_margin_seconds: 30,
    generation: 29_000_000, source_interval: { start: "2026-10-24T18:00:00Z", end: "2026-10-24T18:00:01Z" },
    exposure_upper_bound: "2026-10-24T18:00:01Z", public_not_before: "2026-10-24T18:10:31Z",
    first_published_at: "2026-10-24T18:10:40Z", expires_at: "2026-10-24T19:00:00Z", age_seconds: age,
    images: [
      { variant: "large", url: `/media/previews/${ID}`, content_type: "image/jpeg", width: 1280, height: 580, bytes: 9 },
      { variant: "small", url: `/media/previews/${ID.replace("0f", "1f")}`, content_type: "image/jpeg", width: 480, height: 218, bytes: 4 },
    ],
  });
  if (value === null) throw new Error("fixture");
  return value;
}

test("local aging: fresh through 1,200 s, stale through 1,800 s, then unavailable, never fresher", () => {
  const received = { preview: pointer(700), receivedAt: 0 };
  expect(previewTile(received, false, 500_000)).toMatchObject({ kind: "current", state: "fresh", age: 1200 });
  expect(previewTile(received, false, 500_001)).toMatchObject({ kind: "current", state: "stale" });
  expect(previewTile(received, true, 1_100_000)).toMatchObject({ kind: "current", state: "stale", age: 1800 });
  expect(previewTile(received, true, 1_100_001)).toEqual({ kind: "unavailable", message: "The last capture is more than 30 minutes old." });
  // A server-stale preview stays stale; a clock running backwards never makes it younger.
  expect(previewTile({ preview: pointer(1300, "stale"), receivedAt: 0 }, false, -50_000)).toMatchObject({ state: "stale", age: 1300 });
  // A suspended tab's monotonic clock may stall; wall time since receipt still ages it.
  expect(previewTile({ preview: pointer(700), receivedAt: 0, receivedWall: 0 }, false, 0, 1_100_001)).toMatchObject({ kind: "unavailable" });
  // A wall clock stepped backwards cannot make it younger than monotonic time says.
  expect(previewTile({ preview: pointer(700), receivedAt: 0, receivedWall: 0 }, false, 500_001, -1e9)).toMatchObject({ state: "stale" });
  expect(previewTile(null, false, 0)).toEqual({ kind: "loading" });
  expect(previewTile(null, true, 0)).toEqual({ kind: "unavailable", message: SERVICE_DOWN });
  expect(captureLabel("2026-10-24T18:00:00Z", 725)).toBe("captured 12:00 (12 min ago)");
});

test("an unavailable or malformed pointer never yields an image", () => {
  const unavailable = parsePreview({ ...pointer(1900), state: "unavailable", reason: "too_old" });
  expect(unavailable?.images).toEqual([]);
  expect(parsePreview({ ...pointer(700), state: "brand-new" })?.state).toBe("unavailable");
  expect(parsePreview({ ...pointer(700), images: [{ ...pointer(700).images[0], url: "https://elsewhere/x.jpg" }] })).toBeNull();
});

test("tiles show the image with its capture age, then age out with the network gone", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "performance", "Date"] });
  let up = true;
  const fetchMock = vi.fn(async (url: string) => {
    if (!up) throw new TypeError("network down");
    return new Response(JSON.stringify(url.includes("skycam_east") ? pointer(700) : { ...pointer(0), camera: "skycam_west", state: "unavailable", reason: "no_preview", images: [] }), { status: url.includes("frontyard_1") ? 503 : 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  await act(async () => { render(<>{(["skycam_east", "skycam_west", "frontyard_1"] as const).map((camera) => <PreviewTile key={camera} camera={camera} />)}</>); await vi.advanceTimersByTimeAsync(0); });
  const east = document.querySelector("[data-preview-state]") as HTMLElement;
  expect(east.dataset.previewState).toBe("fresh");
  expect(screen.getByRole("img", { name: "East sky camera, captured 12:00 (11 min ago)" })).toBeTruthy();
  expect(screen.getByText("Preview unavailable. No eligible preview yet.")).toBeTruthy();
  expect(screen.getByText(`Preview unavailable. ${SERVICE_DOWN}`)).toBeTruthy();
  up = false;
  await act(async () => { await vi.advanceTimersByTimeAsync(560_000); });
  expect(east.dataset.previewState).toBe("stale");
  expect(screen.getByText("Stale")).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(600_000); });
  expect(east.dataset.previewState).toBe("unavailable");
  expect(screen.queryAllByRole("img")).toHaveLength(0);
  expect(fetchMock.mock.calls.every(([url]) => /^\/api\/v1\/cameras\/(skycam_east|skycam_west|frontyard_1)\/preview$/.test(url))).toBe(true);
});
