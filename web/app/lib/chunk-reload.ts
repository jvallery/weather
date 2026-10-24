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
  target.addEventListener("vite:preloadError", (event) => { if (reloadOnce(target)) event.preventDefault(); });
  target.addEventListener("unhandledrejection", (event) => {
    const message = event.reason instanceof Error ? event.reason.message : String(event.reason);
    if (FAILED_IMPORT.test(message) && reloadOnce(target)) event.preventDefault();
  });
}
