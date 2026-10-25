// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { VideoPlayer } from "./video-player";
import { parseTimelapses, type Timelapse } from "../lib/weather-api";
import catalog from "../fixtures/catalog.json";
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const published = (): Timelapse => parseTimelapses(catalog)!.items[0];
test("native controls never autoplay, one player, quality change preserves the playhead", () => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  const ready = vi.spyOn(HTMLMediaElement.prototype, "readyState", "get").mockReturnValue(1);
  const load = vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  const video = published();
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
  expect([...element.querySelectorAll("source")].map((source) => source.getAttribute("src"))).toEqual([video.renditions.find((item) => item.role === "native")!.asset.url]);
  expect(load).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("link", { name: /Download native/ }).getAttribute("href")).toBe(video.native_download_url);
  expect(screen.getByRole("link", { name: /Sidecar JSON/ }).getAttribute("href")).toBe(video.sidecar!.url);
});
test("auto quality is selected once by the browser from <source> media queries; a viewport change never reloads", () => {
  const listen = vi.fn();
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true, addEventListener: listen, removeEventListener: vi.fn() })));
  const load = vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  const video = published();
  const url = (role: string) => video.renditions.find((item) => item.role === role)!.asset.url;
  const view = render(<VideoPlayer video={video} />);
  const element = view.container.querySelector("video")!;
  expect(element.hasAttribute("src")).toBe(false);
  expect([...element.querySelectorAll("source")].map((source) => [source.getAttribute("src"), source.getAttribute("media")])).toEqual([[url("mobile"), "(max-width: 767px)"], [url("web"), null]]);
  expect(screen.getByRole("option", { name: "Auto (Mobile)" })).toBeTruthy();
  expect(listen).not.toHaveBeenCalled(); expect(load).not.toHaveBeenCalled();
});
test("seek requests apply once metadata is known and player time is reported, never mid-seek", () => {
  const ready = vi.spyOn(HTMLMediaElement.prototype, "readyState", "get").mockReturnValue(0);
  const onTime = vi.fn();
  const video = published();
  const view = render(<VideoPlayer video={video} seek={{ seconds: 13.6083, serial: 1 }} onTime={onTime} />);
  const element = view.container.querySelector("video")!;
  Object.defineProperty(element, "duration", { configurable: true, value: 60 });
  expect(element.currentTime).toBe(0);
  ready.mockReturnValue(1); fireEvent.loadedMetadata(element);
  expect(element.currentTime).toBeCloseTo(13.6083, 6);
  Object.defineProperty(element, "seeking", { configurable: true, value: true });
  fireEvent.timeUpdate(element); expect(onTime).not.toHaveBeenCalled();
  Object.defineProperty(element, "seeking", { configurable: true, value: false });
  fireEvent.seeked(element); expect(onTime).toHaveBeenCalledWith(element.currentTime);
  view.rerender(<VideoPlayer video={video} seek={{ seconds: 1.5, serial: 2 }} onTime={onTime} />);
  expect(element.currentTime).toBe(1.5);
});
test("a partial-coverage recording plays with a visible coverage label", () => {
  const video = { ...published(), state: "partial" as const };
  const view = render(<VideoPlayer video={video} coverageLabel="Partial coverage · 62.5% of the source interval was captured; this recording has gaps." />);
  const element = view.container.querySelector("video")!;
  expect(element.getAttribute("poster")).toBe(video.poster!.url);
  expect(screen.getByText(/^Partial coverage · 62\.5% of the source interval was captured/)).toBeTruthy();
  expect(element.getAttribute("aria-describedby")).toContain("coverage-label");
  expect(screen.getByRole("link", { name: /Download native/ })).toBeTruthy();
});
test("a product missing its media set, withdrawn or unavailable exposes no player or poster", () => {
  for (const video of [{ ...published(), state: "partial" as const, poster: null }, { ...published(), state: "unavailable" as const }, { ...published(), withdrawn: true }, { ...published(), renditions: published().renditions.slice(1) }]) {
    const view = render(<VideoPlayer video={video} />);
    expect(view.container.querySelector("video, img")).toBeNull();
    expect(screen.getByText(/^No playable media/)).toBeTruthy();
    view.unmount();
  }
});
test("playing a recording pauses any other player", () => {
  const view = render(<><video data-testid="other" /><VideoPlayer video={published()} /></>);
  const other = screen.getByTestId("other") as HTMLVideoElement; other.pause = vi.fn();
  fireEvent.play(view.container.querySelectorAll("video")[1]); expect(other.pause).toHaveBeenCalledOnce();
});
