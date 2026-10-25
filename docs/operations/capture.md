# Snapshot collector operation

Three collectors (`collector-skycam-east`, `collector-skycam-west`, `collector-frontyard-1`)
write sealed chunks to the Obsidian staging share (#4). They are the only input to the
renderer (#15), keograms, hourly stills and delayed previews. Public requests never
trigger camera access.

## Deployment

Source: `deploy/k8s/capture/`, rendered by Argo Application `weather`.

- PVC `weather-staging` binds the storage-infra#1106 PV (300 GiB hard quota, soft
  NFSv4.1 mount, Retain, `Prune=false,Delete=false`). Each collector mounts it once at
  `/staging`; `tmp/` and `sealed/` must stay on that one mount (a cross-mount seal rename
  fails with EXDEV).
- StatefulSets `collector-<camera>`, `replicas: 1`, image `weather-app` at the pinned digest,
  command `weather capture collect --camera <id>`. They run as UID 1025 / GID 100 (the share
  owner), read-only root FS, all capabilities dropped, seccomp RuntimeDefault, no SA token,
  priority `estate-app`, grace period 30 s.
- ConfigMap `collector-capture-<hash>` (from `capture/capture.json`) plus the Downward API
  `POD_UID` are projected read-only at `/etc/weather/capture`. The hash suffix rolls the
  three collectors on any change. `phase_ms` defaults per camera: 0, 333, 667 ms.
- Secret `weather-camera-<camera>` (agents KSOPS overlay) is mounted as files at
  `/run/secrets/camera`, mode 0440.
- Network: namespace `default-deny`, NetworkPolicy `collector` (metrics ingress on 9000 from
  `observability` only, no egress), and the agents-owned `collector-<camera>-egress`, which
  selects `app.kubernetes.io/name: collector-<camera>` and allows that camera's address and
  snapshot port only. No DNS, database, Garage or API access. The NFS mount is made by the
  kubelet, not the pod.
- Metrics: the `weather-app` scrape job keeps every `part-of=weather` pod with a `metrics`
  port, so the collectors are scraped without extra config.

## Cadence (config epoch)

| Epoch | `cadence_ms` | Reason |
|---|---:|---|
| 2026-10-25 | 2000 (0.5 Hz) | Jason's decision: staging capacity before #19 cleanup exists. At the default 180° shutter rule the 60 fps timelapse loses no quality at 0.5 Hz. |

1 Hz (`cadence_ms: 1000`) is the target once #19 cleanup runs in production: edit
`capture.json`, merge, and record the new epoch in this table and in D02. The change
alters `config_hash`, so chunks before and after are distinguishable.

## Staging occupancy plan

Until #19 cleanup runs, nothing removes sealed chunks, so the share only fills. Measured fill
rate and time to 70% are in [D02](../decisions/D02-camera-sources-and-time.md#commissioned-measurements).
Watch `weather_capture_staging_ratio` (or `df` on `/staging` inside a collector). If it
approaches 0.70, scale all three collectors to 0 through Git (below) and leave them there
until #19 runs or Jason chooses another bound. Do not delete anything to make room: sealed
chunks are renderer input, and tmp files belong to scheduler reconciliation (#19). At 0.90
the collector itself stops writing JPEG payloads (below).

## Runbook

- **Pause/resume one camera or all:** set `replicas: 0` (or back to 1) in
  `deploy/k8s/capture/<camera>.yaml`, merge, and let Argo sync. On SIGTERM the collector seals
  its current chunk within the grace period. Never `kubectl scale` against Argo except as an
  emergency stop, then follow with the Git change.
- **Cadence change:** edit `capture.json`, record the epoch above and in D02, merge.
- **Credential rotation (with Sentinel):** the camera credential is the Sentinel/Frigate
  default; rotate it with the sentinel owner, then update the agents `secrets-ksops/weather`
  Secret. Restart each collector after the new Secret is live (projected files update, but the
  collector reads them once at start). Expect `authentication` results until both sides match.
- **NAS outage:** the soft mount returns errors; the collector abandons its own chunk, backs
  off and records `persist_failed` loss ranges. Gaps are explicit; nothing spills to node
  storage. Restore the NAS (storage-infra); no collector action is needed.
- **Staging full:** see the occupancy plan. Do not delete sealed input.
- **Frigate regression:** if Frigate camera fps, skipped fps, errors or restarts regress for
  `skycam_east`, `skycam_west` or `frontyard_1`, scale all three collectors to 0 through Git
  immediately, then investigate with the sentinel owner.

## Collector behaviour

Run `weather capture collect --camera <id>` with a camera-specific read-only
`/etc/weather/capture` and `/run/secrets/camera` mount. Camera IDs are `skycam_east`,
`skycam_west`, `frontyard_1`. Only camera ID and mount paths belong in argv.
The Secret contains exactly the owner-provided `CAMERA_HOST`, `CAMERA_PORT`,
`CAMERA_USER`, `CAMERA_PASSWORD`. The host must be literal; DNS and arbitrary URLs
are unsupported. The only request path is channel-1 `/cgi-bin/snapshot.cgi`.
HTTPS uses system certificate validation; do not disable TLS verification.

`capture.json` contains `staging` (absolute mount path), `geometry` (commissioned
width/height), optional `cadence_ms` (default 1000), `phase_ms` (0/333/667 per
camera), `chunk_seconds` (60, maximum 300), timeout/body bounds and optional private
cache calibration revision/allowance. Mount `POD_UID` separately through the
Downward API. Do not mount writable configuration or inject credentials through
environment variables. The container needs writes only under its staging directory;
the image disables bytecode writes. Actual read-only-root-container qualification
is part of the commissioned workload check.

The metrics listener defaults to port 9000. `/livez` and `/readyz` fail after a
filesystem operation has remained blocked for more than 30 seconds, independently
of the collector event loop. Probe readiness does not imply fresh frames or a
healthy camera. `/metrics` exposes `camera` labels, plus a bounded `result`:

- `weather_capture_last_good_frame_age_seconds` (`-1` before a successful capture)
- `weather_capture_attempts_total{camera,result}`, where `result` is one fixed value:
  `captured`, a failure class (`timeout`, `transport`, `protocol`, `authentication`,
  `redirect`, `http_status`, `content_type`, `oversized`, `truncated`, `jpeg_markers`,
  `geometry`), `overdue`, `backoff`, `staging_full`, `persist_failed` or `shutdown`.
  Every value is exported at zero from start-up
- `weather_capture_request_seconds`, `weather_capture_request_bytes`
- `weather_capture_seal_seconds`, `weather_capture_abandoned_chunks_total`,
  `weather_capture_seal_uncertain_total`
- `weather_capture_staging_ratio`

At 90% occupancy the collector records `staging_full` and stops JPEG payload writes.
Metadata continues within its bounded chunk buffer; writes resume only below 75%.
Do not delete unpublished input to clear pressure. Restore the storage dependency
or use the owning cleanup/recovery workflow. The collector never spills frames onto
worker root storage. Camera authentication failures require owner investigation;
the collector waits at least 60 seconds before another attempt.

A storage error (for example ENOSPC, EDQUOT, ESTALE or EIO on write, seal or statvfs)
abandons the current chunk, unlinks that collector's own tmp file and backs off
1/2/4/8/16/30 seconds. During the backoff the collector makes no camera request and
no NFS call; each slot is counted as `persist_failed` and summarized in the next
sealed chunk's `loss_range`. The backoff resets after the next successful seal. A
steady `persist_failed` rate with an empty tmp directory therefore means the share
is refusing writes: check the PV quota and the NAS, not the collector.

A camera-controlled status line outside 100-599, or a malformed header block, is
recorded as `protocol` and the loop continues. Repeated response headers are
combined; only a repeated `Content-Length` or `Transfer-Encoding` is a `protocol`
failure. A 401 may offer several challenges; only the Digest challenge is used.
Exit status 2 with "Invalid mounted capture configuration" means only that the
mounted configuration or Secret failed to load.

On SIGTERM the current HTTP exchange is cancelled, its attempt is recorded and the
current complete chunk is sealed if storage permits. If storage cannot retain a
ledger, later successful chunks carry explicit persistence-loss ranges. A hard-killed
writer leaves its tmp file and uncertain filename-window evidence, not an eligible
sealed product; tmp cleanup belongs to the scheduler (#19). See the [v2 contract](../contracts/capture-contract.md) for the
reconciliation rule and post-rename fsync uncertainty. Never adopt, modify or remove
another incarnation's file from a collector.

Stop/recover a single camera by scaling its exact commissioned StatefulSet only;
retain sealed and uncertain tmp input for scheduler reconciliation. Restart does not
require database or camera management access. Camera egress rules and private
addresses stay in the encrypted owner overlay. Rule/probe extension is tracked with
commissioning and monitoring-stack#1072; no alert delivery is claimed by this file.

Validation: `scripts/ci.sh lint typecheck unit` covers small pure checks. The
`integration` mode requires `WEATHER_CI_IN_CLUSTER=1` and runs the fake-camera
Digest, five-second trickle, nonoverlap, malformed-response and body-cap cases.
Never run those HTTP/latency tests or image builds on the Mac.

The collector does not decode frames. It checks size, `Content-Type: image/jpeg`,
SOI/EOI markers and that the SOF geometry equals the mounted native geometry, then
stores the original bytes unchanged. The renderer decodes every frame with FFmpeg
and records a decode failure as a reject for that slot
([#15](https://github.com/jvallery/weather/issues/15) acceptance).
