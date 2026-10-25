# Public Weather API

The `api` Deployment (2 replicas, `weather` namespace) serves `/api/v1/weather/{current,metrics,observations,aggregates,almanac}` `POST /api/v1/search` ([search operations](search.md)) and the event feed `/api/v1/events` ([events runbook](events.md)) read-only from `weather-pg` as `weather_api`. Contract: [api-contract.md](../contracts/api-contract.md#weather-data-routes-implemented-14). Code: `app/weather/api/`.

## How it works

- **Database.** The pod carries `weather.vallery.net/database-role=api`, which admits egress to `weather-pg` (NetworkPolicy `weather-database-clients`). Credentials come from `weather-pg-api` and the CA from `weather-pg-ca`, mounted at `/var/run/weather-db`. TLS is verify-full against `weather-pg-rw`. The pool has 4 connections per pod with no overflow, so two replicas plus a surge pod stay far below the role's limit of 40. Every read is a `READ ONLY` transaction with `statement_timeout` 1.5 s (aggregates 10 s, at most 2 at a time per pod, queued). It reads only the `public_api` views from migration `0002_api`.
- **Start-up.** The process waits up to 60 s for the first connection (shared helper `wait_for_database`), then refreshes current conditions every 5 s in the background. If the database is unreachable it still starts, stays unready and keeps retrying.
- **Probes.** Startup and liveness use `/healthz`, which never touches the database, so a database outage never restarts pods. Readiness (`/readyz`) passes while the database answered within 30 s, or while a cached reading still has a fresh value.
- **Current conditions.** Requests are served from the in-process reading; no request hits the database. The newest `obs_st` and `rapid_wind` rows come from the `source_ts` btrees (never a scan). Rain last hour and rain today (America/Denver day) are sums of `precip_accumulated` with 60 s support per report; a window with no reports is null, not 0.
- **Freshness defaults.** `obs_st` 60 s cadence, fresh for 150 s; `rapid_wind` 3 s, fresh for 15 s. Override with `WEATHER_API_OBS_ST_INTERVAL_SECONDS`, `WEATHER_API_OBS_ST_HOLD_SECONDS`, `WEATHER_API_RAPID_WIND_INTERVAL_SECONDS` and `WEATHER_API_RAPID_WIND_HOLD_SECONDS`.
- **Timelapse catalog.** `/api/v1/timelapses` and `/api/v1/timelapses/{video_id}` (#18) read only `public_api.videos`, `public_api.assets` (migration `0003_publication`) and `public_api.video_analyses` (`0012_analysis`, the labelled AI description; [intelligence](intelligence.md)) on the same pool. Pages hold at most 100 entries, newest first, filtered by camera, segment, state and one of `cycle_date`, `month` or `from`/`to` (at most 366 days). Responses are `no-store` and carry `/media/{asset_id}` URLs only. Publication and withdrawal: [media operations](media.md).
- **Hourly stills.** `/api/v1/stills` (#38) reads only `public_api.stills` and `public_api.assets` (migration `0009_stills`). One calendar `date` (optionally one local `hour`, 0–23, and a `camera`), or one `camera` and `hour` over `from`/`to` (at most 31 days). Every UTC hour slot of those days appears (23, 24 or 25 per day, from the pinned time model): `on_target`, `off_target`, `missing` (with its reason), `withdrawn`, or `pending` until the pipeline has selected it and its capture upper bound + 630 s has passed; an unselected slot older than 12 h is `missing`/`not_selected`. At most 75 items, `no-store`, `/media/{asset_id}` image URLs only. Runbook: [media operations](media.md#hourly-originals-and-comparison-stills).
- **Keograms.** `/api/v1/keograms?cycle_date=YYYY-MM-DD[&camera=][&segment=]` (#37) reads only `public_api.keograms` and `public_api.assets` (`0008_keograms`): at most six items, `no-store`. Each has the exact bin map (1,440 equal half-open bins over the segment), gap runs, lineage digest, recipe and region version, the PNG, thumbnail and sidecar URLs (only once published), and per-bin seek targets (`seek.output_seconds[i]`, the midpoint of the video frame holding bin i's midpoint), null unless the video is playable. States: `pending`, `published`, `partial`, `unavailable`, `failed`, `withdrawn`; `public_api.videos.keogram_state` uses the same values.
- **Media origin.** `/media/{asset_id}` and `/downloads/{video_id}/native` (#20) are answered by `app/weather/media/` ahead of the FastAPI routes, authorized per request against `public_api.assets`/`public_api.videos` on the same pool, and streamed from Garage `weather-media` with the optional read-only key. Runbook: [media operations](media.md#serve).
- **Cursors.** Signed with `CURSOR_HMAC_KEY` from Secret `weather-api-cursor` (mounted only into `api`). Rotating the key invalidates outstanding export and catalog cursors (clients get 400 `invalid_cursor` and restart the export); nothing else depends on it.
- **Export snapshot.** The first page records the database clock minus 120 s; every page then reads only rows whose `inserted_at` (set by the database on insert, migration `0002_api`) is at or before it. A spool drain or `reproject` that lands old rows mid-export therefore never shifts the export. The newest two minutes of rows appear only in a new export. Rows that existed before `0002_api` read `inserted_at = -infinity`.

## Almanac

`GET /api/v1/weather/almanac` (#42) serves one `product` per request: `record`, `rain_day`, `wind_rose_bin`, `astronomy` or `comparison`. The web page is `/weather/almanac/`. Code: `WeatherService.almanac` and `app/weather/products/almanac.py`.

- **Computed on read (C5).** Every product runs the same aggregate SQL as `/aggregates` (day bins for records, rain days and comparison missing days; one whole-interval bin for the rose and comparison values). Nothing is stored, so a corrected or late row changes the next read, and `evidence_count`/`evidence_hash` change with the inputs. There is no recompute job.
- **Bounds.** Intervals start and end at America/Denver midnight and cover at most 366 local days (comparison windows likewise); misaligned or longer windows are 400s before any SQL runs. Records and comparisons use one-minute `obs_st` metrics only. A year-long window is one aggregate statement under the same 10 s timeout and two-per-pod queue as `/aggregates`; pages hold up to 400 items and use the signed export cursor.
- **Records** rank only days with ≥95% metric-duration coverage (`almanac-v1`); ties share a rank. Lower-coverage days stay in the response with `below_record_coverage`. `history_interval` starts at the first valid `obs_st` report of any device at the site and ends at the read time.
- **Astronomy** uses the private site from Secret `weather-site` (key `site.json`, the `load_site` JSON format; see [time model](time-model.md)), mounted optionally at `/var/run/weather-site`. Civil dawn/dusk, sunrise and sunset are exactly `solar_cycle`'s values (including any commissioned dawn/dusk offsets). Moon phase is Astral's value for the calendar date (evaluated at 00:00 UTC), named in eight equal sectors. Without the Secret, or with an invalid file (logged without values), astronomy items carry `site_not_configured` and only the moon phase. Coordinates and the site's `config_hash` never appear in a response. Creating or rotating the Secret is agents KSOPS work; restart `deploy/api` afterwards to load it.

```sh
curl -s 'https://weather.vallery.net/api/v1/weather/almanac?product=rain_day&start=2026-10-24T06:00:00Z&end=2026-10-25T06:00:00Z' \
  | jq '{history_interval, coverage, evidence_count, items: [.items[] | {local_date, value, coverage: .coverage.fraction}]}'
curl -s 'https://weather.vallery.net/api/v1/weather/almanac?product=astronomy&start=2026-10-24T06:00:00Z&end=2026-10-25T06:00:00Z' \
  | jq '.missing_reasons, .items[0].sunrise'
```

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
| `/current` 503, pods ready | Database answers but the views are missing (migration not applied) or there is no reading yet | Check the `weather-migrate` hook Job and `SELECT version_num FROM alembic_version` (expect the head, `0008_keograms` at #37). |
| `data_state: cached` | Database reads failing while the cache is fresh | As above; values keep their original times and turn stale on schedule. |
| `data_state: unavailable` and all metrics null | No telemetry yet (receiver not live) | Expected before #5; not an API fault. |
| Aggregates 503 | 10 s statement timeout or queueing | Narrow the window or coarsen the resolution; watch `weather_api_db_errors_total{operation="aggregates"}`. |
| Almanac 503 | Same timeout or queue on a long window | Use a shorter local-date window; watch `weather_api_db_errors_total{operation="almanac"}`. |
| Almanac sun times null with `site_not_configured` | Secret `weather-site` absent or invalid | Check the Secret exists with key `site.json` and restart `deploy/api`; the API log says when the file is invalid. |
| `invalid_cursor` on a running export | Cursor idle > 1 h, export > 24 h, changed filters or rotated key | Restart the export from its first page. |
