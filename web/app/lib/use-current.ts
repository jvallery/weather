// SPDX-License-Identifier: MIT
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { currentSchema, type Current } from "./contracts";
import { CURRENT_QUERY_KEY, nextPollDelay } from "./polling";

export function useCurrent(initial: Current) {
  const [visible, setVisible] = useState(true);
  const failures = useRef(0);
  const [successfulPoll, setSuccessfulPoll] = useState(false);
  const client = useQueryClient();
  useEffect(() => {
    const changed = () => {
      const foreground = document.visibilityState !== "hidden";
      setVisible(foreground);
      if (!foreground) void client.cancelQueries({ queryKey: CURRENT_QUERY_KEY });
    };
    changed();
    document.addEventListener("visibilitychange", changed);
    return () => document.removeEventListener("visibilitychange", changed);
  }, [client]);
  useEffect(() => {
    setSuccessfulPoll(false);
    if (initial.data_state !== "unavailable") client.setQueryData(CURRENT_QUERY_KEY, initial);
  }, [client, initial]);
  const result = useQuery({
    queryKey: CURRENT_QUERY_KEY, initialData: initial, staleTime: Infinity,
    enabled: visible, retry: false, refetchOnMount: false, refetchOnWindowFocus: false,
    refetchOnReconnect: false, refetchIntervalInBackground: false,
    refetchInterval: () => nextPollDelay(failures.current),
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
      }
    },
  });
  return { ...result, loaderUnavailable: initial.data_state === "unavailable" && !successfulPoll };
}
