#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# weather#33: the weather-pg restore drill from the B2 custody copy.
#   scripts/restore-drill.sh plan        print the rendered drill manifest (no cluster access)
#   scripts/restore-drill.sh run <id>    run one drill now, as a Job from the CronJob, inside
#                                        namespace weather, and print its receipt
# The drill itself is deploy/k8s/restore-drill/restore_drill.py; it runs only in the cluster,
# so the read-only keys are referenced in place and never leave namespace weather.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
kc=(kubectl --context "${KUBE_CONTEXT:-vallery}" --namespace weather)
case "${1:-}" in
  plan)
    exec python3 "$root/deploy/k8s/restore-drill/restore_drill.py" --plan ;;
  run)
    id="${2:?usage: restore-drill.sh run <id>}"
    [[ "$id" =~ ^[a-z0-9]([a-z0-9-]{0,22}[a-z0-9])?$ ]] || { echo "id: 1-24 character DNS slug" >&2; exit 2; }
    job="weather-pg-restore-drill-$id"
    "${kc[@]}" create job "$job" --from=cronjob/weather-pg-restore-drill
    # A restore takes about 20 minutes; wait in bounded steps (the Job deadline is 3 h).
    until "${kc[@]}" wait "job/$job" --for=jsonpath='{.status.completionTime}' --timeout=8m 2>/dev/null \
      || [[ -n "$("${kc[@]}" get "job/$job" -o jsonpath='{.status.failed}')" ]]; do :; done
    "${kc[@]}" logs "job/$job" | grep '^weather_pg_restore_drill_receipt '
    [[ "$("${kc[@]}" get "job/$job" -o jsonpath='{.status.succeeded}')" == 1 ]] ;;
  *)
    echo "usage: restore-drill.sh plan | run <id>" >&2; exit 2 ;;
esac
