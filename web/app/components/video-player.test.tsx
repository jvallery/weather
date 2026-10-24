// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { VideoPlayer } from "./video-player";
import { catalogSchema } from "../lib/contracts";
import catalog from "../fixtures/catalog.json";
afterEach(cleanup);
test("native controls never autoplay, one player, quality change preserves the playhead", () => {
  const video = catalogSchema.parse(catalog).items[0];
  const view = render(<VideoPlayer video={video} />);
  const element = view.container.querySelector("video")!;
  expect(view.container.querySelectorAll("video")).toHaveLength(1);
  expect(element.hasAttribute("controls")).toBe(true); expect(element.hasAttribute("autoplay")).toBe(false);
  Object.defineProperty(element, "duration", { configurable: true, value: 60 });
  Object.defineProperty(element, "paused", { configurable: true, value: true });
  element.currentTime = 12.5;
  fireEvent.change(screen.getByLabelText("Playback quality"), { target: { value: "mobile" } });
  element.currentTime = 0;
  fireEvent.change(screen.getByLabelText("Playback quality"), { target: { value: "native" } });
  fireEvent.loadedMetadata(element);
  expect(element.currentTime).toBe(12.5);
  expect(element.src).toContain("native"); expect(screen.getByRole("link", { name: /Download native/ }).getAttribute("href")).toContain("/native");
});
test.each(["partial", "unavailable", "expired"] as const)("%s exposes no player or poster", (state) => {
  const video = catalogSchema.parse(catalog).items[0];
  const view = render(<VideoPlayer video={{ ...video, state }} />);
  expect(view.container.querySelector("video, img")).toBeNull();
});
test("playing a recording pauses any other player", () => {
  const video = catalogSchema.parse(catalog).items[0]; const view = render(<><video data-testid="other" /><VideoPlayer video={video} /></>);
  const other = screen.getByTestId("other") as HTMLVideoElement; other.pause = vi.fn();
  fireEvent.play(view.container.querySelectorAll("video")[1]); expect(other.pause).toHaveBeenCalledOnce();
});
