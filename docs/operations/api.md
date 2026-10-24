# Public Weather API

The `api` Deployment (2 replicas, `weather` namespace) serves `/api/v1/weather/{current,metrics,observations,aggregates}` read-only from `weather-pg` as `weather_api`. Contract: [api-contract.md](../contracts/api-contract.md#weather-data-routes-implemented-14). Code: `app/weather/api/`.

## How it works

- **Database.** The pod carries `weather.vallery.net/database-role=api`, which admits egress to `weather-pg` (NetworkPolicy `weather-database-clients`). Credentials come from `weather-pg-api` and the CA from `weather-pg-ca`, mounted at `/var/run/weather-db`. TLS is verify-full against `weather-pg-rw`. The pool has 4 connections per pod with no overflow, so two replicas plus a surge pod stay far below the role's limit of 40. Every read is a `READ ONLY` transaction with `statement_timeout` 1.5 s (aggregates 10 s, at most 2 at a time per pod, queued). It reads only the `public_api` views from migration `0002_api`.
- **Start-up.** The process waits up to 60 s for the first connection (shared helper `wait_for_database`), then refreshes current conditions every 5 s in the background. If the database is unreachable it still starts, stays unready and keeps retrying.
- **Probes.** Startup and liveness use `/healthz`, which never touches the database, so a database outage never restarts pods. Readiness (`/readyz`) passes while the database answered within 30 s, or while a cached reading still has a fresh value.
- **Current conditions.** Requests are served from the in-process reading; no request hits the database. The newest `obs_st` and `rapid_wind` rows come from the `source_ts` btrees (never a scan). Rain last hour and rain today (America/Denver day) are sums of `precip_accumulated` with 60 s support per report; a window with no reports is null, not 0.
- **Freshness defaults.** `obs_st` 60 s cadence, fresh for 150 s; `rapid_wind` 3 s, fresh for 15 s. Override with `WEATHER_API_OBS_ST_INTERVAL_SECONDS`, `WEATHER_API_OBS_ST_HOLD_SECONDS`, `WEATHER_API_RAPID_WIND_INTERVAL_SECONDS` and `WEATHER_API_RAPID_WIND_HOLD_SECONDS`.
- **Cursors.** Signed with `CURSOR_HMAC_KEY` from Secret `weather-api-cursor` (mounted only into `api`). Rotating the key invalidates outstanding export cursors (clients get 400 `invalid_cursor` and restart the export); nothing else depends on it.

## Signals

Port 9000 `/metrics`:

| Metric | Meaning |
|---|---|
| `weather_current_conditions_age_seconds` | Now minus the newest valid `obs_st` source time. Absent until an `obs_st` row exists. |
| `weather_metric_source_age_seconds{metric}` | Source age of each current metric that has a value. |
| `weather_api_request_duration_seconds{route,status}` | Latency per public route. |
| `weather_api_current_cache_total{result}` | Current responses served `live`, `cached` (DB down) or `unavailable` (503). |
| `weather_api_db_errors_total{operation}` | Failed or timed-out reads per operation. |

## Checks

```sh
kubectl --context vallery -n weather get deploy api -o wide
kubectl --context vallery -n weather get pods -l app.kubernetes.io/component=api
curl -sI https://weather.vallery.net/api/v1/weather/current | grep -iE '^(HTTP|etag|cache-control)'
curl -s https://weather.vallery.net/api/v1/weather/current | jq '{data_state, metrics: [.metrics[] | {metric, value, freshness}]}'
kubectl --context vallery -n weather exec deploy/api -- python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9000/metrics').read().decode())" \
  | grep -E '^weather_(current_conditions_age|api_db_errors)'
```

## Failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| `/current` 503, pods unready | Database unreachable for > 30 s and no fresh cache | `kubectl cnpg status weather-pg -n weather`; check the `weather-database-clients` NetworkPolicy and the `weather-pg-api` Secret. Pods recover on their own when the database answers. |
| `/current` 503, pods ready | Database answers but the views are missing (migration not applied) or there is no reading yet | Check the `weather-migrate` hook Job and `SELECT version_num FROM alembic_version` (expect `0002_api`). |
| `data_state: cached` | Database reads failing while the cache is fresh | As above; values keep their original times and turn stale on schedule. |
| `data_state: unavailable` and all metrics null | No telemetry yet (receiver not live) | Expected before #5; not an API fault. |
| Aggregates 503 | 10 s statement timeout or queueing | Narrow the window or coarsen the resolution; watch `weather_api_db_errors_total{operation="aggregates"}`. |
| `invalid_cursor` on a running export | Cursor idle > 1 h, export > 24 h, changed filters or rotated key | Restart the export from its first page. |
