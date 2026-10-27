# D02 — native camera snapshots and qualified source time

> **Historical.** Binding rules live in [docs/architecture.md](../architecture.md) and the 2026-10-24 [simplification](2026-10-24-simplification.md) and [takeover](2026-10-24-takeover-replan.md) records; where this file conflicts, those win.

> **Amended 2026-10-24** by the [2026-10-24 simplification record](2026-10-24-simplification.md): Unchanged in substance. Camera access is via view-only users (sentinel#550); cadence is commissioned in WX-03. Where this ADR conflicts with that record or [architecture.md](../architecture.md), they win.


> **Config epoch 2026-10-27 (#4; Jason's decision):** production cadence is **0.5 Hz (2,000 ms grid) with a 180° synthetic shutter**, and the 1 Hz commissioning run is waived. Measured at 0.5 Hz over 24 h ending 2026-10-27 15:00Z (Prometheus; no identifiers):
>
> | Camera | Request p50 / p95 / p99 | Frame bytes p50 / p95 / p99 | Staging ingest |
> |---|---|---|---|
> | skycam_east | 1.57 / 2.74 / 2.95 s | 957 / 3,734 / 4,024 KiB | 51.0 GB/day |
> | skycam_west | 1.45 / 2.69 / 2.95 s | 965 / 3,743 / 4,025 KiB | 47.4 GB/day |
> | frontyard_1 | 1.47 / 2.71 / 2.96 s | 1,984 / 3,885 / 4,054 KiB | 73.3 GB/day |
>
> - **Requests:** every poll is two round trips (the cameras invalidate each Digest nonce), so p90 of about 2.4–2.6 s is the accepted floor. Geometry is native 4096×1856. Last-good-frame age stays under 2 s.
> - **Misses:** overdue is 0.33–0.38 % per camera since the #145 deploy (2026-10-26 20:38Z); timeout and backoff are under 0.05 %. The 24 h window above includes the eight hours before that deploy.
> - **Fresh frames:** 99.0–99.1 % of slots per camera over the 18.4 h after the #145 deploy, counting slots lost to deploy restarts.
> - **CPU:** collectors use 0.03–0.05 cores each and pipelines 0.05–0.12 cores each (24 h average).
> - **Storage:** staging ingest is about 172 GB/day across three cameras and is released within about an hour of integration (#19). The 300 GB quota (storage-infra#1106) was sized for 1 Hz, so it has wide headroom. Persistent growth is about 2.85 GB of published media per daily cycle plus about 0.12 GB/day of hourly originals: about 1.1 TB/year at 365-day video retention.
> - **Frigate:** no regression; the numbers are on sentinel#550.

Status: **three snapshot endpoints verified; full-view/no-mask public policy approved; sustained cadence, source-time calibration and keogram-region qualification pending**. Requirements R03–R06/R08/R30–R32; WX-09/10/36/37/38/39. Camera ownership and commissioning are tracked by [OWN-CAM](https://github.com/jvallery/sentinel/issues/550); no camera settings or NVR deployment changed.

## Selected adapter and E06

Use the fixed-camera channel-1 snapshot API with Digest authentication, scoped credentials and no redirects. Prefer verified HTTPS where the device's qualified configuration supports it; otherwise use the approved camera-network HTTP path. Collector destinations are fixed private configuration, never URLs supplied by public clients. Do not use Frigate's latest-frame endpoint, retained video extraction or a persistent RTSP decoder as the acquisition source.

On 2026-10-23, sequential bounded authenticated GETs returned decodable JPEGs from all three selected cameras. Exact addresses, source revision, payload hashes and request metadata remain in the private E06 receipt; images are retained only on the private operator surface.

| Camera | Successful JPEG dimensions | Bytes | Response elapsed seconds | Observed limitation |
|---|---|---:|---:|---|
| skycam_east | 4096 × 1856 | 2,060,757 | 0.662 | First request timed out with a one-second socket timeout; one retry with three seconds succeeded. |
| skycam_west | 4096 × 1856 | 1,614,699 | 1.253 | One sample; elapsed includes authentication/transfer, not a sustained-rate measurement. |
| frontyard_1 | 4096 × 1856 | 3,295,643 | 0.525 | One sample; byte size is scene/encoding dependent. |

E06 receipt SHA-256: `4bb04638b879a02ebbc54f0cce67b4829e1dd9c287ecc2223292168862414418`. The initial probe's missing optional HTTP client library caused no camera request; the completed probes used the standard-library Digest client. JPEGs were fully decoded for this small bounded check. No request loop, rate test, camera write, credential change or media publication occurred.

These dimensions establish the observed JPEG output, not independently verified maximum native sensor/main-stream parity. Current source configuration uses smaller Frigate detect geometry for the sky cameras; that does not constrain Weather's observed snapshot dimensions or authorize changing Frigate. Device model/firmware, full native-mode parity, NVR impact and source timestamp trust remain owner qualification items.

## Capacity consequence

The three successful samples total 6,971,099 bytes. If those exact sizes occurred continuously at one sample/second per camera, payload alone would be about 602.3 decimal GB/day. Forty-eight hours plus 30% reserve would be about 1.566 TB before scratch, metadata and other NAS obligations. This is a sample-based arithmetic illustration, not measured daily load, p95 sizing or a requested grant. It shows why the earlier 0.5 MB example cannot determine admission. OWN-NFS must use commissioned per-camera distributions, backlog and scratch demand.

Preserve the 1 Hz commissioning target with one in-flight request per camera, bounded total deadlines, staggered phases and skipped overdue slots. A response exceeding a second does not trigger overlapping requests or a catch-up burst. Qualify connect/read/total deadlines and the 16 MiB initial payload ceiling against all lighting regimes. Rate/size limits must fail visibly; do not lower camera resolution or omit native originals to fit a presumed budget.

## Commissioned measurements

Config epoch **2026-10-25: `cadence_ms` 2000 (0.5 Hz)**, phases 0/333/667 ms, 60 s chunks,
geometry 4096 × 1856 (Jason's decision, #4). Reason: staging capacity before #19 cleanup
exists; at the default 180° shutter rule the 60 fps timelapse loses no quality at 0.5 Hz.
1 Hz is the target once #19 runs. Runbook: [capture](../operations/capture.md#cadence-config-epoch).

Night window, 2026-10-25 03:15–04:15 UTC, one hour per camera after the JPEG marker fix
(#110), from the sealed chunk manifests (no identifiers):

| Camera | Frames/h | Poll p50 / p95 / p99 (s) | Bytes p50 / p95 | Failures |
|---|---:|---|---|---|
| skycam_east | 885 | 3.58 / 4.02 / 4.05 | 849 kB / 902 kB | none |
| skycam_west | 880 | 3.96 / 4.05 / 4.92 | 895 kB / 912 kB | 3 timeouts |
| frontyard_1 | 876 | 3.75 / 4.04 / 4.63 | 806 kB / 814 kB | 2 timeouts |

All frames 4096 × 1856, every frame a distinct hash (fresh-frame rate 1.0). Collector CPU
17–98 m, memory 40–50 MiB.

- **JPEG structure.** All three cameras write a COM segment whose length undercounts its
  payload (≈600 extraneous bytes before the next marker); the frame still ends exactly at
  EOI. The marker walk skips them as libjpeg and FFmpeg do (#110).
- **Digest.** The cameras issue single-use nonces: every frame is a 401 (~30 ms) and an
  authorized retry (~0.7 s transfer). The original client paced that retry a full cadence
  after the challenge, so a poll took cadence + transfer and every other slot was overdue:
  0.25 Hz realised at the 0.5 Hz epoch, with p99 near the 5 s deadline. #119 paces polls
  instead of exchanges; expected poll time is then ~0.7 s at night.
- **NAS cost.** Measured fill at the realised 0.25 Hz night rate: ~59 GB/day (statvfs). At
  0.5 Hz, night frames give ~113 GB/day; the daytime E06 sizes (above) give ~301 GB/day; a
  day/night mix is ~200 GB/day, so the 300 GiB share reaches 70% in about 26 h without #19
  cleanup. At 1 Hz that halves to about 13 h.
- **Cadence × shutter.** The 7,200-frame cycle gives a source step of about 12 s for a 12 h
  segment, so the default 180° shutter is about 6 s wide (D10). Each capture supports half
  the expected interval on each side, so at 0.5 Hz about three captures fill the shutter
  (coverage 1.0); 1 Hz blends about six for smoother motion at twice the NAS cost. A
  realised 0.25 Hz against a 2 s expected interval covers only about half of each shutter,
  below the 0.90 minimum, so #119 must be live before #15 renders this input.
- **Frigate.** Before (01:13 UTC) and after (04:15 UTC): camera and process fps 5.0 on all
  three, skipped fps 0, no detect or record errors, Frigate container restarts unchanged.
  skycam_west's audio-only ffmpeg reconnected three times between 03:09 and 03:34 UTC (one
  other, unpolled camera reconnected once in the same window); its video was unaffected.
- **Pending.** Civil-dawn, midday and civil-dusk windows, the #16 corpus, and the
  post-#119 cadence measurement.

## Time and derived products

Use the [capture contract](../contracts/capture-contract.md) for slot, wall-clock and monotonic epochs, source-time estimate, uncertainty, freshness evidence and manifest ownership. HTTP Date and arrival time do not establish exposure time; equal JPEG hashes alone do not establish staleness. A camera timestamp needs validation; otherwise measure caching/latency and calibrate the request-midpoint estimate. Record unbounded uncertainty honestly and deny public image eligibility while its latest plausible exposure cannot be trusted.

Freeze local timezone/site and civil-twilight model revisions for each cycle. Assign observations to one half-open segment by qualified source time, independently of request slot. Calendar-day/hour identities use actual elapsed local hours, with repeated offsets preserved; hourly selection can cross twilight while retaining its own source pins. No temporal render context crosses a segment merely because adjacent captures share a TAR. Keep exact 7,200-frame/60-fps cycle allocation and the accepted UTC/local-label distinction.

Keogram regions and horizon exclusions must be versioned per camera after day/twilight/night qualification. The owner-approved public visual-mask policy is explicitly none for all three full views, as recorded below. Do not reuse Frigate motion/detection masks as privacy policy or assume one sky strip suits the frontyard camera. Preserve genuine source lineage, missing bins and accessible source-time seeking. Public derivatives strip private metadata and enforce the latest plausible exposure bound plus 600 seconds; private originals remain inaccessible from public media routes.

## Acceptance and rollback

OWN-CAM/WX-10 require admitted day/twilight/night samples of latency, byte/geometry distributions, actual fresh-frame rate, clock/cache behavior, errors and existing NVR health before/during/after collection. Test stale responses, malformed/oversized JPEGs, Digest failure, slow-trickle bodies, no redirects, timeout, restart/clock discontinuity and no catch-up. A one-second probe timeout is evidence to tune commissioning, not proof the camera is unusable.

WX-09/36/37/38/39 validate boundary/DST/hour identity, per-camera regions, retained original bytes, eligibility and linked navigation with those qualified inputs. Missing/failed cameras remain explicit; no fictional frame or silent alternate source substitutes. Stop a trial by stopping its exact collector, preserve receipts and verify unchanged camera/NVR behavior. No camera rollback should be necessary because collection makes no management writes.

Source exposure bounds do not qualify the authorizing host clock. The [conservative clock contract](../contracts/preview-policy-contract.md#conservative-authorization-clock-bounds) requires exposure upper +600 seconds <= authorizer now lower, and now upper strictly before logical expiry. Keep source calibration and host-clock validity independently bound; unknown or stale clock evidence denies public eligibility. Numeric clock admission and actual adapter behavior remain unqualified.

## Owner-approved public image policy

After review of the retained E06 daylight samples, Jason explicitly selected **“Use all three full views without visual masks.”** Apply this to `skycam_east`, `skycam_west` and `frontyard_1` public timelapses, sanitized comparison stills, delayed previews, video posters and analysis inputs. Preserve the full selected camera field of view and native aspect ratio; do not add privacy crops, opaque regions or blur by inference. This resolves the camera-mask owner question for the reviewed views. No image was uploaded to the public repository or deployed by this planning decision.

Record an explicit versioned `none` visual-mask policy per camera, bound to the selected source geometry/orientation and derivative recipe. An empty mask is an intentional owner selection, not missing configuration. Keogram extraction still selects a weather-relevant sampling region and records that region separately; it is a derived visualization rather than an implicit privacy crop. Existing video rendition/geometry contracts remain unchanged.

Full-view approval does not make native originals public. Keep archived original JPEG bytes private and publish through the derivative validation path, stripping private metadata and excluding exact coordinates, addresses, station identities and credentials from serializers and sidecars. Preserve the minimum 600-second eligibility delay from the latest plausible exposure and current expiry/withdrawal rules. Approved full views may contain household activity; do not add people/object redaction that Jason did not request. Camera-rendered timestamps are image pixels, not proof of trustworthy exposure time; existing source-time calibration still applies.

The policy is bound to the reviewed camera setup. Detect an unexpected source/geometry/orientation change as a configuration mismatch rather than silently treating it as the same approved recipe; follow normal source-change qualification. If Jason later selects masks, bind the new policy revision through every derived output and model input and withdraw incompatible public derivatives using D14. No such mask change is selected now.

Acceptance: verify all three cameras use the explicit no-mask policy, full-view/aspect-ratio preservation, private-original access denial, derivative metadata stripping and unchanged delay/expiry/withdrawal checks. The retained single daylight samples establish the reviewed scene only; they do not prove sustained cadence, night behavior, exposure-time calibration or renderer implementation. Those remain the existing commissioning obligations.
