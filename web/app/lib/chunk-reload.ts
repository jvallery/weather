// SPDX-License-Identifier: MIT
/** Old-tab safety: after a rollout a held tab may request a chunk the new build no longer has. */
const KEY = "weather:chunk-reload";
// Chromium, Firefox and WebKit wordings for a failed dynamic import.
const FAILED_IMPORT = /dynamically imported module|Importing a module script failed/i;

/** Reload once per tab session. Without a recordable guard, never reload (no loops). */
export function reloadOnce(target: Window): boolean {
  try {
    if (target.sessionStorage.getItem(KEY)) return false;
    target.sessionStorage.setItem(KEY, "1");
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
