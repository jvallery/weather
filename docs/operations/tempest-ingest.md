# Tempest receive and drain

`weather tempest-receiver` receives Tempest hub UDP broadcasts on port 50222, commits each datagram to a local SQLite spool, and drains the spool to `weather-pg`. It runs as Deployment `tempest-receiver` in `weather` (#5, `deploy/k8s/receiver/`). Integration, kill and outage tests run only in fleet CI, never on the Mac.

## Deployment and data path

The Tempest hub belongs to jvallery/homeassistant; Weather only listens. The hub broadcasts to 255.255.255.255:50222 on VLAN 10. The pod's second interface `net1` comes from the agents-owned NAD `weather/weather-tempest-vlan10` (Argo Application `multus`): a VLAN 10 child of `enp6s21` holding network-infra#774's reserved address as a /32, with no gateway and no IPv4 routes. The receiver reads datagrams on `net1`, commits them to the spool PVC, and drains over `eth0` to `weather-pg-rw:5432` as `weather_ingest`. Home Assistant receives the same broadcasts independently.

- **Scheduling.** `replicas: 1`, `Recreate`, required affinity to `vm-k3s-node6`/`vm-k3s-node7` (the VLAN 10 trunk workers with the `vlan`/`static` CNI plugins, agents#5368). Only one pod can hold the address and the VLAN child, so never run a second receiver or a qualification pod alongside it.
- **Pod.** Restricted PSA: UID 10001, all capabilities dropped, read-only rootfs, seccomp `RuntimeDefault`, no hostNetwork, no service-account token. `SO_BINDTODEVICE` needs no capability.
- **Mounts.** `/etc/weather/telemetry`: ConfigMap `tempest-receiver` (`receiver.json`: `net1`, 768 MiB spool cap) plus Secret `weather-tempest` (agents KSOPS: `TEMPEST_HUB_SERIAL`, `TEMPEST_DEVICE_SERIALS`; no source IP). `/var/run/weather-db`: `weather-pg-ingest` and `weather-pg-ca`. `/var/lib/weather/telemetry`: PVC `tempest-receiver-spool` (1 GiB `longhorn-v1-rwo`, `Prune=false,Delete=false`).
- **Network policy.** `tempest-receiver` allows egress only to `weather-pg` 5432 and kube-dns, and ingress only to `metrics` (9000) from `observability`. The `weather-app` Prometheus job scrapes it. There is no Service, IngressRoute or other listener for 50222.
- **Residual same-L2 risk.** NetworkPolicy governs `eth0` only. `net1` sits on VLAN 10 L2, whose UDP senders are unauthenticated, so a well-formed spoofed datagram naming the configured serials would be admitted (P4). The app never transmits on `net1`. The kernel does: link-up DAD/MLD/router solicitation, and ARP replies for the reserved address. The kernel also accepts VLAN 10 router advertisements, so `net1` carries a SLAAC IPv6 address and an on-link /64 route (no IPv6 default route). Turning that off would need an `accept_ra`/`disable_ipv6` tuning step in the agents NAD; restricted PSA cannot set it from the pod.
- **Rotating identities.** If a hub or station is replaced, recapture the serials into `weather-tempest` (agents OWNERSHIP, "Weather Tempest receiver custody"), let `weather-secrets` sync, then `kubectl rollout restart deploy/tempest-receiver`. New devices are seeded at drain start.

Measured at commissioning (2026-10-25): `rapid_wind` every 3 s (~101 bytes per receipt), `hub_status` every 20 s (297 bytes), `obs_st` and `device_status` every 60 s (186 and 212 bytes). That is about 36,000 receipts and about 2 MB of raw bytes a day. The first canonical row landed 20 s after pod start.

### Health checks

```sh
kubectl --context vallery -n argocd get application weather          # Synced Healthy
kubectl --context vallery -n weather get pods -l app.kubernetes.io/name=tempest-receiver -o wide
curl -fsS https://weather.vallery.net/api/v1/weather/current | jq '.data_state, [.metrics[].observed_at] | max'
```

`weather_telemetry_received_total`, `_spooled_total`, `_committed_total` and `_projected_total` advance together, every `weather_telemetry_rejected_total{reason}` stays flat, and `weather_telemetry_spool_depth` is 0. Without `ip` in the image, read `net1` from `/proc/net/route` (no `net1` rows), `/proc/net/if_inet6` and `/sys/class/net/net1/statistics/tx_packets`.

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

`terminationGracePeriodSeconds` is 45. On node loss, Kubernetes waits for the node's eviction timeout (about 5 minutes) before rescheduling, and Longhorn must detach the RWO volume from the lost node. Expect a gap of several minutes, and only node6 or node7 can host the pod. If both trunk workers are down, the receiver stays Pending and Home Assistant remains the only listener. `kubectl rollout restart deploy/tempest-receiver` at commissioning left a 12 s `rapid_wind` gap and no `obs_st` gap beyond its 60 s cadence. The new pod drained the one receipt the old pod had left pending, and the spool returned to 0.

## Spool full

Each datagram is one SQLite WAL/`synchronous=FULL` commit. `/ready` fails at 85% of usable capacity (warning at 70%), and at the limit new datagrams are dropped and counted as `weather_telemetry_capacity_loss_total`; pending rows are never overwritten. A full spool means the drain is not committing: fix the database path (next section). Do not delete, vacuum or force-open the spool.

A row that cannot be decoded (checksum or type mismatch) is moved alone to `quarantine/row-*.bin` and counted in `weather_telemetry_spool_quarantined_rows_total` (one lost receipt each); draining continues. If the SQLite file itself fails its integrity check at start, the whole `active/` directory is moved intact to `quarantine/<id>/` and `weather_telemetry_spool_unreadable_generations_total` increments. Quarantined files do not count against capacity; keep them for offline recovery through the storage owner.

## Drain errors

The drain sends up to 128 rows per transaction and acknowledges (deletes) spool rows only after the CNPG commit returns. Each receipt runs in its own SAVEPOINT: a receipt that fails deterministically (parser or data error) is stored raw-only as `parse_state='quarantined'`, `parse_reason='drain_failed'` (or its admission reason, such as `missing_hub_identity`), counted in `weather_telemetry_drain_quarantined_total`, and the batch continues. Connection loss, deadlock or serialization failure, resource exhaustion, statement or lock timeout, and missing privileges (SQLSTATE classes 08, 40, 53, 57, codes 42501 and 55P03) are not the receipt's fault: they fail the whole batch, nothing is acknowledged, and the spool retries it. Counters advance only after the commit.

- `weather_telemetry_drain_errors_total` rising with a growing `weather_telemetry_spool_depth` and `weather_telemetry_drain_lag_seconds`: the database is unreachable or rejecting the ingest role. Leave the receiver running; it retries the same receipt IDs when access returns. Recovery is complete when the spool depth is 0 and the receipt counts in CNPG match.
- `weather_telemetry_drain_quarantined_total` rising: a code or schema defect rejects some receipts. Their raw bytes are kept and the spool keeps emptying, but their typed rows are missing until the cause is fixed and `reproject` runs (below). Recovered receipts keep `parse_state='quarantined'`, because the ingest role cannot update receipts.
- `weather_telemetry_unprojected_total` rising: rows have no matching device (Secret serials and `weather.device` disagree) or no valid source time. Raw receipts are kept.
- After fixing the cause, rebuild typed rows from retained raw bytes inside the receiver pod:

  ```sh
  weather tempest-receiver reproject --since 2026-10-24T00:00Z
  ```

  It seeds devices from the Secret, re-parses every receipt received since that time (except `missing_hub_identity`) in pages along the `(received_at, id)` btree, and inserts missing rows with `ON CONFLICT DO NOTHING`. Existing rows are never changed, so running it twice is safe.

## Clock offset

Rows are keyed by the hub's `source_ts`; the receive time is the node clock. Node time sync (chrony) belongs to proxmox-infrastructure. A source time more than `future_seconds` ahead of the receive time is stored invalid (`future_timestamp`) and hidden publicly. Compare `telemetry_receipt.received_at` with `source_ts` on recent `rapid_wind` rows (join on `receipt_id`) to measure the offset; a steady offset of more than a few seconds means one of the clocks is wrong, and it also shifts the public embargo and capture timing that use node time. At commissioning on vm-k3s-node7, receive minus source time was 0.14–0.93 s (median 0.34 s): the hub's whole-second timestamps plus delivery latency.
