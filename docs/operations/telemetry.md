# Telemetry alerts

The slice-1 alert rules in jvallery/monitoring-stack link here. Each section names the signal and points to the runbook that owns the fix.

## Public probe

`WeatherPublicProbeDown`: blackbox job `weather-probes`, `component="edge"`, failed to load `https://weather.vallery.net/` over TLS. Check the Argo Application and the web and api pods first, then the route: the IngressRoutes in `deploy/k8s/ingress.yaml`, traefik-apps and the Cloudflare tunnel (network-infra). [Deploy](deploy.md#rollback-and-recovery) covers rollback; [public launch](public-launch.md) covers the edge checks.

## Current conditions

`WeatherCurrentConditionsStale`: `weather_current_conditions_age_seconds` is over 90 s while the receiver still reports fresh `obs_st` packets, or the metric is absent. Packets arrive but the API doesn't show them, so the fault is in the drain or the API, not the station. See [drain errors](tempest-ingest.md#drain-errors) and the API's [signals](api.md#signals) and [failure modes](api.md#failure-modes).

## Packet freshness

`WeatherTempestPacketsStale`: `weather_telemetry_last_packet_age_seconds{family="obs_st"}` is over 300 s, or absent. The receiver isn't getting station broadcasts. See [health checks](tempest-ingest.md#health-checks) and [receiver reschedule](tempest-ingest.md#receiver-reschedule). The hub itself belongs to jvallery/homeassistant.

## Clock offset

`WeatherNodeClockOffset`: a GPU worker's clock is more than 0.5 s off, or unsynchronized. Node time sync belongs to jvallery/proxmox-infrastructure. Its effect on Weather is described in [clock offset](tempest-ingest.md#clock-offset).

## Imported history

Station history before the live receiver (first report 2026-10-24 20:39Z) is imported from the verdify-platform greenhouse controller's TimescaleDB (#167). It is `verdify-db-0` in `verdify-prod`, table `public.climate`, and holds this Tempest's outdoor readings from 2025-10-23 in US units. Imported rows live in `weather.obs_import`, one batch per month in `weather.import_batch`. Aggregate reads use `public_api.obs_st_history`, which shows them only before the first live report, so live data always wins. They carry `provenance = 'verdify-platform'` and no receipt. The live views `public_api.obs_st` and `rapid_wind`, current conditions and the raw export are unchanged.

- **What is imported.** Mapping and rules: `app/weather/importer/verdify.py`, tests in `app/tests/test_verdify_import.py`.
  - Temperature, humidity, station pressure, illuminance, solar radiation and UV come from every outdoor row that carries them. Before April 2026, light, solar and UV also came on their own two-minute rows.
  - Each Tempest row's rapid-wind sample (`wind_speed_mph`, `wind_direction_deg`) goes into the rapid-wind history (`family = 'rapid_wind'`, `public_api.rapid_wind_history`). It is held at most 6 s, like a live 3 s sample.
  - Averaged wind, gust, lull and direction come only from Tempest rows (`wind_speed_avg_mph` present).
  - Rain and lightning come only from Tempest rows that also carry `precip_intensity_in_h`. Home Assistant poll rows put a rate in `precip_in`.
  - Out-of-range values are dropped, never clamped.
- **Coverage.** The source reported every 1 to 5 minutes, so each imported reading is held up to `cap_seconds` (600 s). Live readings keep the registry's two-interval cap. Rain exists only for minutes the station reported, so imported rain coverage is low by design: an unreported minute is a gap, never zero.
- **Run or re-run.** `scripts/import_verdify_history.sh 2025-10 2026-10` handles one month per batch:
  1. A read-only `COPY` out of verdify-db, using the owner role in a read-only transaction. Run it with no new credentials or network policy.
  2. The transform in the weather app pod.
  3. One transaction into weather-pg.

  It needs the app image with `weather.importer` (0016 or later). A rerun inserts nothing (`ON CONFLICT (source, family, source_ts) DO NOTHING`) and records the rows as duplicates in its batch.
- **Check.** `SELECT source_start, source_rows, inserted_rows, overlap_rows, duplicate_rows, state FROM weather.import_batch ORDER BY source_start`. Per day, `count(*)` in `weather.obs_import` equals the source's outdoor rows that carried a valid reading, less any overlap with live. `/api/v1/weather/aggregates` and the almanac return an `imported` range (the pages show the history without a label, at Jason's request, 2026-10-28).
- **Roll back a batch.** As the migrator, in one transaction:
  1. `DELETE FROM weather.obs_import WHERE batch_id = '<id>'`
  2. `UPDATE weather.import_batch SET state = 'rolled_back' WHERE id = '<id>'`

  This deletes imported rows only. Live `obs_st` is never touched. It is data deletion, so it needs Jason's decision line first.
