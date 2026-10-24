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
healthy camera. `/metrics` exposes camera-only application labels:

- `weather_capture_last_good_frame_age_seconds` (`-1` before a successful capture)
- `weather_capture_attempts_<result>_total` for captured, authentication, transport,
  timeout, protocol, validation, overdue, backoff, staging pressure and persistence
  outcomes; fixed metric names avoid arbitrary error labels
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

On SIGTERM the current HTTP exchange is cancelled, its attempt is recorded and the
current complete chunk is sealed if storage permits. If storage cannot retain a
ledger, later successful chunks carry explicit persistence-loss ranges. A hard-killed
writer leaves tmp data and uncertain filename-window evidence, not an eligible
sealed product. See the [v2 contract](../contracts/capture-contract.md) for the
reconciliation rule and post-rename fsync uncertainty. Never adopt, modify or remove
another incarnation's file from a collector.

Stop/recover a single camera by scaling its exact commissioned StatefulSet only;
retain sealed and uncertain tmp input for scheduler reconciliation. Restart does not
require database or camera management access. Camera egress rules and private
addresses stay in the encrypted owner overlay. Rule/probe extension is tracked with
commissioning and monitoring-stack#1072; no alert delivery is claimed by this file.

Validation: `scripts/ci.sh lint typecheck unit` covers small pure checks. The
`integration` mode requires `WEATHER_CI_IN_CLUSTER=1` and runs the fake-camera
Digest, five-second trickle, nonoverlap, malformed/native JPEG and body-cap cases.
Never run those HTTP/latency/decode tests or image builds on the Mac.

The selected full decoder is `simplejpeg` 1.9.0 (MIT), using its bundled
libjpeg-turbo with `strict=True`; simplejpeg and NumPy 2.5.4 are hash-locked.
The [dependency review](../decisions/capture-decoder-reuse.md) records the native
runtime licenses and retained notices. The [upstream contract](https://github.com/jfolz/simplejpeg#decode_jpeg)
explicitly rejects recoverable codec errors in strict mode. Set the commissioned
container's `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1` to bound the NumPy
runtime; no matrix/BLAS workload is part of capture. Original bytes are never
resized, re-encoded or metadata-mutated. Full decoded dimensions must still match
the mounted native geometry. The corruption fixture preserves SOI/EOI/SOF/SOS
headers while damaging the entropy stream; marker checks alone cannot pass it.
