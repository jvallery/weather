// SPDX-License-Identifier: MIT
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { currentSchema, type Current } from "./contracts";
import { CURRENT_QUERY_KEY, nextPollDelay } from "./polling";

export type CurrentPollingOptions = { refreshOnResume?: boolean; jitterFraction?: number };
export function useCurrent(initial: Current, apiUnavailable = false, options: CurrentPollingOptions = {}) {
  const refreshOnResume = options.refreshOnResume === true;
  const jitter = Math.min(0.2, Math.max(0, options.jitterFraction || 0));
  const wasVisible = useRef(true);
  const [resumes, setResumes] = useState(0);
  const [visible, setVisible] = useState(true);
  const failures = useRef(0);
  const pollDelay = useRef<number | null>(null);
  // Source-age ticks render every second. Keep this sample stable until a
  // request completes so QueryObserver does not continually restart its timer.
  if (pollDelay.current === null) {
    pollDelay.current = Math.min(120_000, Math.round(nextPollDelay(0) * (1 + jitter * (2 * Math.random() - 1))));
  }
  const [successfulPoll, setSuccessfulPoll] = useState(false);
  const client = useQueryClient();
  useEffect(() => {
    const changed = () => {
      const foreground = document.visibilityState !== "hidden";
      if (foreground && !wasVisible.current && refreshOnResume) setResumes((count) => count + 1);
      wasVisible.current = foreground;
      setVisible(foreground);
      if (!foreground) void client.cancelQueries({ queryKey: CURRENT_QUERY_KEY });
    };
    changed();
    document.addEventListener("visibilitychange", changed);
    return () => document.removeEventListener("visibilitychange", changed);
  }, [client, refreshOnResume]);
  useEffect(() => {
    setSuccessfulPoll(false);
    // A successful unavailable/null response invalidates older values. A transport
    // failure retains the previously received data and its original source times.
    if (!apiUnavailable) {
      void client.cancelQueries({ queryKey: CURRENT_QUERY_KEY });
      client.setQueryData(CURRENT_QUERY_KEY, initial);
    }
  }, [apiUnavailable, client, initial]);
  const result = useQuery({
    queryKey: CURRENT_QUERY_KEY, initialData: initial, staleTime: Infinity,
    enabled: visible, retry: false, refetchOnMount: false, refetchOnWindowFocus: false,
    refetchOnReconnect: false, refetchIntervalInBackground: false,
    refetchInterval: () => pollDelay.current!,
    queryFn: async ({ signal }) => {
      try {
        const response = await fetch("/api/v1/weather/current", { signal: AbortSignal.any([signal, AbortSignal.timeout(6000)]), cache: "no-store", headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error("Current conditions unavailable");
        const current = currentSchema.parse(await response.json());
        failures.current = 0;
        setSuccessfulPoll(true);
        return current;
      } catch (error) {
        if (!signal.aborted) failures.current += 1;
        throw error;
      } finally {
        pollDelay.current = Math.min(120_000, Math.round(nextPollDelay(failures.current) * (1 + jitter * (2 * Math.random() - 1))));
      }
    },
  });
  useEffect(() => {
    if (refreshOnResume && resumes > 0 && visible) void client.refetchQueries({ queryKey: CURRENT_QUERY_KEY, type: "active" }, { cancelRefetch: false });
  }, [client, refreshOnResume, resumes, visible]);
  return { ...result, loaderUnavailable: apiUnavailable && !successfulPoll };
}
