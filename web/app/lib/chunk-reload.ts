// SPDX-License-Identifier: MIT
/** Old-tab safety: after a rollout a held tab may request a chunk the new build no longer has. */
const KEY = "weather:chunk-reload";
// Chromium, Firefox and WebKit wordings for a failed dynamic import.
const FAILED_IMPORT = /dynamically imported module|Importing a module script failed/i;

// A reload that fails again inside this window surfaces the error; a later rollout gets a fresh recovery.
const WINDOW_MS = 60_000;

/** Reload at most once per window. Without a recordable guard, never reload (no loops). */
export function reloadOnce(target: Window): boolean {
  const now = Date.now();
  try {
    const last = Number(target.sessionStorage.getItem(KEY));
    if (last && Math.abs(now - last) < WINDOW_MS) return false;
    target.sessionStorage.setItem(KEY, String(now));
  } catch { return false; }
  target.location.reload();
  return true;
}
export function installChunkReload(target: Window) {
  // Leaving the page aborts its in-flight imports. Those failures are not a missing chunk, and a
  // reload then would cancel the navigation the visitor started and reload the page being left.
  let leaving = false;
  target.addEventListener("beforeunload", () => { leaving = true; });
  target.addEventListener("pagehide", () => { leaving = true; });
  target.addEventListener("pageshow", () => { leaving = false; });
  const recover = () => !leaving && reloadOnce(target);
  target.addEventListener("vite:preloadError", (event) => { if (recover()) event.preventDefault(); });
  target.addEventListener("unhandledrejection", (event) => {
    const message = event.reason instanceof Error ? event.reason.message : String(event.reason);
    if (FAILED_IMPORT.test(message) && recover()) event.preventDefault();
  });
}
