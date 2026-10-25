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
