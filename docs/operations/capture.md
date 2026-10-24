# Snapshot collector operation

WX-10 provides source and fake-camera qualification. WX-03 commissions the real
cameras, per-camera PVC/mounts, NetworkPolicies, cadence and workload activation.
Do not use a public request to trigger capture. No camera commissioning or live
camera qualification is implied by a passing fake-camera test.

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
