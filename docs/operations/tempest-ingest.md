# Tempest receive and drain

`weather tempest-receiver` receives Tempest hub UDP broadcasts on port 50222, commits each datagram to a local SQLite spool, and drains the spool to `weather-pg`. #5 commissions the workload; this page describes the code on `main`. Integration, kill and outage tests run only in fleet CI, never on the Mac.

## Configuration

Project a public `receiver.json` and Secret `weather-tempest` into `/etc/weather/telemetry` (override with `TELEMETRY_CONFIG_DIRECTORY`).

- `receiver.json` requires `interface_name` (the NAD interface, `net1`). Optional: `hub_source_port`, `spool_directory` (default `/var/lib/weather/telemetry`), `capacity_bytes` (default 1 GiB), `future_seconds` (default 120), `metrics_port` (default 9000). Private fields and duplicate keys are rejected.
- Secret keys:
  - `TEMPEST_HUB_SERIAL`: the hub serial (required).
  - `TEMPEST_DEVICE_SERIALS`: a JSON array of 1–32 distinct ST serials (required).
  - `TEMPEST_HUB_SOURCE`: the hub IPv4 address (optional). When present, datagrams from any other source are rejected as `wrong_source`.

Values are bounded printable ASCII and are never trimmed, inferred or accepted from argv. Errors, metrics and object representations omit them.

## Admission

The socket is bound to the NAD interface with `SO_BINDTODEVICE` (Linux ≥5.7, no capability needed) and a wildcard address, so broadcasts are delivered; the `IP_PKTINFO` interface index is checked again per datagram. A datagram is spooled only if it is at most 4 KiB, arrived on that interface, and is a JSON object naming the configured hub or a configured ST serial. Everything else is counted under `weather_telemetry_rejected_total{reason}`: `oversized`, `wrong_interface`, `wrong_source`, `malformed`, `wrong_identity`. A device packet without `hub_sn` is kept raw-only as `missing_hub_identity` and never projected. The receiver never transmits. Same-L2 spoofing of a well-formed packet remains a documented residual risk (P4).

## Tables

The ingest role (`weather_ingest`) has SELECT/INSERT only.

- `weather.telemetry_receipt`: one row per datagram: `raw_bytes`, `sha256` (checked by constraint), `received_at`, `parse_state` (`parsed`, `unsupported`, `quarantined`), `parse_reason`, `parser_revision`, `receiver_incarnation`. Raw bytes are authoritative and kept indefinitely.
- `weather.<family>` (`obs_st`, `rapid_wind`, `evt_strike`, `evt_precip`, `device_status`, `hub_status`, legacy `obs_air`, `obs_sky`): typed `values` plus a compact `quality` `{"valid", "reason", "invalid": [field names]}`. The key is `(device_id, message_type, source_ts, row_idx)` with `ON CONFLICT DO NOTHING`, so the first receipt's row wins and replays are no-ops.
- `weather.station` / `weather.device`: at each drain start the receiver inserts, if absent, station `home` (site `local`) plus device `hub` and `tempest` (`tempest-2`, … for further serials) from the Secret, valid from 2020-01-01. Serials live only in `device.private_id`.
- `public_api.observations`: non-status families with public aliases. A value is shown only when its row is `valid` and the field is not listed in `invalid`; otherwise it is null. Future-timestamp rows (more than `future_seconds` after receipt) are stored with `valid=false`.

Rain: `obs_st`/`obs_sky` `precip_accumulated` is the amount over the 60 s ending at `source_ts`. `wind_avg` covers the report interval ending at `source_ts`. Other fields are instantaneous samples (`Row.support` in `weather.telemetry.parse`).

## Coverage and gaps

Gaps come from `source_ts` on the family tables, never from a per-receipt scan. For example, gaps over 90 s in `obs_st` during the last day:

```sql
SELECT prev, source_ts, source_ts - prev AS gap FROM (
  SELECT source_ts, lag(source_ts) OVER (ORDER BY source_ts) AS prev
  FROM weather.obs_st WHERE source_ts > now() - interval '1 day') s
WHERE source_ts - prev > interval '90 seconds';
```

`weather_telemetry_last_packet_age_seconds{family}` is the receive age of the newest packet in this process; replay never freshens it.

## Receiver reschedule

The Deployment is `Recreate` with one replica, and the spool PVC is RWO with a local owner lock, so only one receiver ever writes. A reschedule or node loss leaves a gap of about 1–2 minutes, visible as a `source_ts` gap; nothing on the hub buffers it. Pending spool rows survive the restart and drain under their original receipt IDs. Use at least 40 s termination grace so the drain's bounded transaction finishes.

## Spool full

Each datagram is one SQLite WAL/`synchronous=FULL` commit. `/ready` fails at 85% of usable capacity (warning at 70%), and at the limit new datagrams are dropped and counted as `weather_telemetry_capacity_loss_total`; pending rows are never overwritten. A full spool means the drain is not committing: fix the database path (next section). Do not delete, vacuum or force-open the spool.

A row that cannot be decoded (checksum or type mismatch) is moved alone to `quarantine/row-*.bin` and counted in `weather_telemetry_spool_quarantined_rows_total` (one lost receipt each); draining continues. If the SQLite file itself fails its integrity check at start, the whole `active/` directory is moved intact to `quarantine/<id>/` and `weather_telemetry_spool_unreadable_generations_total` increments. Quarantined files do not count against capacity; keep them for offline recovery through the storage owner.

## Drain errors

The drain sends up to 128 rows per transaction and acknowledges (deletes) spool rows only after the CNPG commit returns. Each receipt runs in its own SAVEPOINT: a receipt that fails deterministically is stored raw-only as `parse_state='quarantined'`, `parse_reason='drain_failed'` (`weather_telemetry_drain_quarantined_total`) and the batch continues.

- `weather_telemetry_drain_errors_total` rising with a growing `weather_telemetry_spool_depth` and `weather_telemetry_drain_lag_seconds`: the database is unreachable or rejecting the ingest role. Leave the receiver running; it retries the same receipt IDs when access returns. Recovery is complete when the spool depth is 0 and the receipt counts in CNPG match.
- `weather_telemetry_unprojected_total` rising: rows have no matching device (Secret serials and `weather.device` disagree) or no valid source time. Raw receipts are kept.
- After fixing either cause, rebuild typed rows from retained raw bytes inside the receiver pod:

  ```sh
  weather tempest-receiver reproject --since 2026-10-24T00:00Z
  ```

  It seeds devices from the Secret, re-parses every receipt received since that time (except `missing_hub_identity`) and inserts missing rows with `ON CONFLICT DO NOTHING`. Existing rows are never changed, so running it twice is safe.

## Clock offset

Rows are keyed by the hub's `source_ts`; the receive time is the node clock. Node time sync (chrony) belongs to proxmox-infrastructure. A source time more than `future_seconds` ahead of the receive time is stored invalid (`future_timestamp`) and hidden publicly. Compare `telemetry_receipt.received_at` with `source_ts` on recent `rapid_wind` rows (join on `receipt_id`) to measure the offset; a steady offset of more than a few seconds means one of the clocks is wrong, and it also shifts the public embargo and capture timing that use node time.
