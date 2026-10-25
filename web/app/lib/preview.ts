// SPDX-License-Identifier: MIT
/**
 * Delayed camera previews (#39). The server authorizes each preview; the client only ever ages
 * one it already received, from the server's `age_seconds` plus monotonic time since receipt,
 * so a client clock never makes imagery look fresher. A failed poll keeps the last preview
 * until it ages out; it never extends what the server authorized.
 */
import { useEffect, useState } from "react";
import { parsePreview, type Preview } from "./weather-api";

export type PreviewTile =
  | { kind: "loading" }
  | { kind: "unavailable"; message: string }
  | { kind: "current"; state: "fresh" | "stale"; preview: Preview; age: number };
export type Received = { preview: Preview; receivedAt: number };

const REASONS: Record<string, string> = {
  no_preview: "No eligible preview yet.",
  too_old: "The last capture is more than 30 minutes old.",
  media_unavailable: "The preview image cannot be read right now.",
};
export const SERVICE_DOWN = "The preview service is not answering.";

export function previewTile(received: Received | null, failed: boolean, now: number): PreviewTile {
  if (received === null) return failed ? { kind: "unavailable", message: SERVICE_DOWN } : { kind: "loading" };
  const { preview } = received;
  if (preview.state === "unavailable" || preview.age_seconds === null) return { kind: "unavailable", message: REASONS[preview.reason || "no_preview"] || REASONS.no_preview };
  const age = preview.age_seconds + Math.max(0, now - received.receivedAt) / 1000;
  if (age > preview.stale_seconds) return { kind: "unavailable", message: REASONS.too_old };
  // Never fresher than the server said.
  const state = preview.state === "stale" || age > preview.fresh_seconds ? "stale" : "fresh";
  return { kind: "current", state, preview, age };
}

export function captureLabel(lower: string, age: number): string {
  const time = new Intl.DateTimeFormat("en-US", { timeZone: "America/Denver", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(lower));
  return `captured ${time} (${Math.floor(age / 60)} min ago)`;
}

const monotonic = () => performance.now();
export function usePreview(camera: string, clock: () => number = monotonic) {
  const [received, setReceived] = useState<Received | null>(null);
  const [failed, setFailed] = useState(false);
  const [now, setNow] = useState(clock);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const poll = async () => {
      let delay = 60_000;
      if (document.visibilityState !== "hidden") {
        try {
          const response = await fetch(`/api/v1/cameras/${camera}/preview`, { cache: "no-store", headers: { Accept: "application/json" }, signal: AbortSignal.any([controller.signal, AbortSignal.timeout(6000)]) });
          const preview = response.ok ? parsePreview(await response.json()) : null;
          if (stopped) return;
          if (preview === null) setFailed(true);
          else { setReceived({ preview, receivedAt: clock() }); setFailed(false); delay = Math.min(300, Math.max(15, preview.refresh_seconds)) * 1000; }
        } catch {
          if (stopped) return;
          setFailed(true);
        }
      }
      setNow(clock());
      timer = setTimeout(() => void poll(), delay);
    };
    void poll();
    const ticker = setInterval(() => setNow(clock()), 15_000);
    return () => { stopped = true; controller.abort(); clearTimeout(timer); clearInterval(ticker); };
  }, [camera, clock]);
  return previewTile(received, failed, now);
}
