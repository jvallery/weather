// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { VideoPlayer } from "./video-player";
import { catalogSchema } from "../lib/contracts";
import catalog from "../fixtures/catalog.json";
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
test("native controls never autoplay, one player, quality change preserves the playhead", () => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  const ready = vi.spyOn(HTMLMediaElement.prototype, "readyState", "get").mockReturnValue(1);
  const video = catalogSchema.parse(catalog).items[0];
  const view = render(<VideoPlayer video={video} />);
  const element = view.container.querySelector("video")!;
  expect(view.container.querySelectorAll("video")).toHaveLength(1);
  expect(element.hasAttribute("controls")).toBe(true); expect(element.hasAttribute("autoplay")).toBe(false);
  Object.defineProperty(element, "duration", { configurable: true, value: 60 });
  Object.defineProperty(element, "paused", { configurable: true, value: true });
  element.currentTime = 12.5;
  fireEvent.change(screen.getByLabelText("Playback quality"), { target: { value: "mobile" } });
  element.currentTime = 0; ready.mockReturnValue(0);
  fireEvent.change(screen.getByLabelText("Playback quality"), { target: { value: "native" } });
  ready.mockReturnValue(1); fireEvent.loadedMetadata(element);
  expect(element.currentTime).toBe(12.5);
  expect(element.src).toContain("native"); expect(screen.getByRole("link", { name: /Download native/ }).getAttribute("href")).toContain("/native");
});
test("a partial-coverage recording plays with a visible coverage label", () => {
  const published = catalogSchema.parse(catalog).items[0];
  const video = catalogSchema.parse({ ...catalog, items: [{ ...published, state: "partial", coverage: { ...published.coverage, fraction: 0.625 } }] }).items[0];
  const view = render(<VideoPlayer video={video} />);
  const element = view.container.querySelector("video")!;
  expect(element.getAttribute("poster")).toBe(video.poster!.asset.url);
  expect(screen.getByText(/^Partial coverage · 62\.5% of the source interval was captured/)).toBeTruthy();
  expect(element.getAttribute("aria-describedby")).toContain("coverage-label");
  expect(screen.getByRole("link", { name: /Download native/ })).toBeTruthy();
});
test("partial products must carry the playable media set", () => {
  const published = catalogSchema.parse(catalog).items[0];
  expect(catalogSchema.safeParse({ ...catalog, items: [{ ...published, state: "partial", poster: null }] }).success).toBe(false);
});
test.each(["unavailable", "expired"] as const)("%s exposes no player or poster", (state) => {
  const video = catalogSchema.parse(catalog).items[0];
  const view = render(<VideoPlayer video={{ ...video, state }} />);
  expect(view.container.querySelector("video, img")).toBeNull();
  expect(screen.queryByText(/Partial coverage/)).toBeNull();
});
test("playing a recording pauses any other player", () => {
  const video = catalogSchema.parse(catalog).items[0]; const view = render(<><video data-testid="other" /><VideoPlayer video={video} /></>);
  const other = screen.getByTestId("other") as HTMLVideoElement; other.pause = vi.fn();
  fireEvent.play(view.container.querySelectorAll("video")[1]); expect(other.pause).toHaveBeenCalledOnce();
});
