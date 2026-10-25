// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { afterEach, expect, test, vi } from "vitest";
import { installChunkReload, reloadOnce } from "./chunk-reload";

function fakeWindow(storage: Pick<Storage, "getItem" | "setItem">) {
  const target = new EventTarget() as unknown as Window;
  const reload = vi.fn();
  Object.defineProperty(target, "sessionStorage", { get: () => storage });
  Object.defineProperty(target, "location", { value: { reload } });
  return { target, reload };
}
function memoryStorage() {
  const values = new Map<string, string>();
  return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
}
function rejection(reason: unknown) {
  const event = new Event("unhandledrejection", { cancelable: true }) as PromiseRejectionEvent;
  Object.defineProperty(event, "reason", { value: reason });
  return event;
}
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

test("a failed preload or dynamic import reloads exactly once, and a later rollout recovers again", () => {
  vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(new Date("2026-10-31T18:00:00Z"));
  const { target, reload } = fakeWindow(memoryStorage());
  installChunkReload(target);
  const preload = new Event("vite:preloadError", { cancelable: true });
  target.dispatchEvent(preload);
  expect(reload).toHaveBeenCalledOnce(); expect(preload.defaultPrevented).toBe(true);
  // The reloaded page fails again: the guard lets the error surface instead of looping.
  const again = new Event("vite:preloadError", { cancelable: true });
  target.dispatchEvent(again);
  target.dispatchEvent(rejection(new TypeError("Failed to fetch dynamically imported module: https://weather.example/assets/x.js")));
  expect(reload).toHaveBeenCalledOnce(); expect(again.defaultPrevented).toBe(false);
  vi.advanceTimersByTime(59_000);
  target.dispatchEvent(new Event("vite:preloadError", { cancelable: true }));
  expect(reload).toHaveBeenCalledOnce();
  // The same tab, still open at the next promotion, gets one fresh recovery reload.
  vi.advanceTimersByTime(2_000);
  const later = new Event("vite:preloadError", { cancelable: true });
  target.dispatchEvent(later);
  expect(reload).toHaveBeenCalledTimes(2); expect(later.defaultPrevented).toBe(true);
});

test("imports aborted by leaving the page never reload over the navigation; a restored page recovers again", () => {
  const { target, reload } = fakeWindow(memoryStorage());
  installChunkReload(target);
  target.dispatchEvent(new Event("beforeunload"));
  const preload = new Event("vite:preloadError", { cancelable: true });
  const aborted = rejection(new TypeError("error loading dynamically imported module: https://weather.example/assets/uPlot.js"));
  target.dispatchEvent(preload); target.dispatchEvent(aborted);
  expect(reload).not.toHaveBeenCalled(); expect(preload.defaultPrevented).toBe(false); expect(aborted.defaultPrevented).toBe(false);
  target.dispatchEvent(new Event("pageshow"));
  target.dispatchEvent(new Event("vite:preloadError", { cancelable: true }));
  expect(reload).toHaveBeenCalledOnce();
});

test.each([
  "Failed to fetch dynamically imported module: https://weather.example/assets/x.js",
  "error loading dynamically imported module: https://weather.example/assets/x.js",
  "Importing a module script failed.",
])("recognizes the engine wording %s", (message) => {
  const { target, reload } = fakeWindow(memoryStorage());
  installChunkReload(target);
  const event = rejection(new TypeError(message));
  target.dispatchEvent(event);
  expect(reload).toHaveBeenCalledOnce(); expect(event.defaultPrevented).toBe(true);
});

test("unrelated rejections and unavailable storage never reload", () => {
  const { target, reload } = fakeWindow(memoryStorage());
  installChunkReload(target);
  target.dispatchEvent(rejection(new Error("Current conditions unavailable")));
  target.dispatchEvent(rejection(undefined));
  expect(reload).not.toHaveBeenCalled();
  const blocked = fakeWindow({ getItem: () => { throw new DOMException("blocked", "SecurityError"); }, setItem: () => {} });
  expect(reloadOnce(blocked.target)).toBe(false); expect(blocked.reload).not.toHaveBeenCalled();
});
