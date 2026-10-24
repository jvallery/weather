// SPDX-License-Identifier: MIT
/** One cadence, bounded backoff. TanStack Query owns in-flight request deduplication. */
export const CURRENT_QUERY_KEY = ["weather", "current"] as const;
export const POLL_INTERVAL = 15_000;
export function nextPollDelay(failures: number): number {
  return Math.min(120_000, POLL_INTERVAL * 2 ** Math.min(3, Math.max(0, failures)));
}
