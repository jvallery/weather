# Timelapse pipeline

`app/weather/pipeline/` (#15) turns each camera's sealed capture chunks into one
timelapse per civil-twilight segment, and (#38) archives one private native original per UTC
hour with sanitized comparison stills ([hourly stills](media.md#hourly-originals-and-comparison-stills)): a frozen schedule, a streaming
observed-exposure renderer, a near-lossless mezzanine, three H.264 renditions,
posters, a sidecar and validation, published through #18
([media operations](media.md)). Live use starts at #49 with the cameras (#4),
the staging PVC and the `weather-media` writer key.

## Schedule and segment states

The scheduler's minute tick enqueues idempotent jobs:

| Key | Due | Effect |
|---|---|---|
| `schedule:<local date>` | at once | Freeze `solar_cycle` for today and the next 3 local days, plus 6 `segment` rows per cycle (3 cameras × daytime/overnight) |
| `finalize:<UTC hour of b>` | boundary b + 30 s | `scheduled` → `processing` for segments ending at b |
| `deadline:<UTC hour of b>` | b + 2 h | Each unfinished segment without a video gets a database-only `unavailable` video (reason `deadline_missed`, no assets) through #18's fenced commit, so `/api/v1/timelapses` shows it; then segment states follow the committed videos, and the rest become `unavailable`/`deadline_missed` |

Cycles are written once and never changed (`site`, `cycle_date` key; the
private site hash and Astral/tzdata versions are provenance only). A
recomputation that disagrees is counted (`weather_schedule_failures_total{reason="drift"}`)
and ignored. When a new day's recomputed dawn differs from the frozen previous
next dawn (a rotated `dawn_offset_seconds` or a library upgrade), the new cycle
starts at the frozen next dawn and its allocation is recomputed from its real
lengths (`weather_schedule_anchored_total`); only a cycle that cannot start
there fails (`reason="discontinuity"`). Current segments of one camera can never
overlap (exclusion constraint). A clock correction is a new segment revision,
never an edit; sealed manifests never change.

Every camera gets its rows whether its pipeline runs or not, so a stopped
camera shows `unavailable`/`deadline_missed` in the public catalog two hours
after each boundary while the others publish. A pipeline that finishes later
(up to 12 h after the boundary, or after a lost acknowledgement) still
publishes: its video supersedes the placeholder as the next revision.

The pipeline sets `published` or `partial` (reason `partial_coverage` at
90–99% slot coverage, `low_coverage` at 50–90%) or `unavailable`
(`insufficient_coverage` below 50%, `no_exposure` if no frame passed the
exposure threshold), and stores the `RenderResult` in `segment.render_result`.

### The site Secret

The schedule reads the private site from Secret `weather-site` (key
`site.json`, the `load_site` JSON format, see [time model](time-model.md)),
mounted optionally at `/var/run/weather-site` in the scheduler. Without it the
scheduler still starts, but every `schedule:` job fails visibly:
`weather_schedule_site_configured` is 0,
`weather_schedule_failures_total{reason="site"}` grows, the log says
"Private site configuration unavailable" and the job ends `failed`. No cycle
is invented. Dawn and dusk offsets beyond ±3600 s are rejected. Create or
rotate the Secret through agents KSOPS, then restart `deploy/scheduler`; the
next tick freezes the missing days.

## Rendering

Recipe v1 (D10, `recipe.py`): synthetic shutter 180° by default (270°/360°
selectable with `WEATHER_PIPELINE_SHUTTER_DEGREES`; a non-default angle or
threshold is a new recipe id and a new render directory), minimum exposure
coverage 0.90 inclusive, slot thresholds 99/90/50.

- **Time.** Segment `[a, b)` with N frames: output frame k is centred at
  `a + (k + ½)(b − a)/N` and shown at PTS `k/60`, all in rational arithmetic.
  A frame at b belongs to the next segment. No context crosses a segment.
- **Exposure.** Each capture at t supports
  `[max(t − d/2, midpoint to previous, a), min(t + d/2, midpoint to next, b))`
  (d = capture interval). Frame k averages, in linear light (float32, sRGB
  tables), every capture time-weighted by its overlap with the shutter
  `step·θ/360`. Coverage is that overlap over the intended width; below 0.90
  the frame is the versioned slate (`flat-srgb-24/v1`) at its own PTS. Time
  never compresses.
- **Intake.** Chunks are read strictly (`read_chunk`: frames plus
  `manifest.jsonl`, each member bound by length and sha256). Frames are keyed
  by #75's canonical grid slot. Per-slot rejects: `settings` (cadence/phase
  differ from `WEATHER_PIPELINE_CADENCE_MS`/`_PHASE_MS`), `unknown_timing` (no
  capture upper bound), `geometry`, `stale`, `changed` (size or hash differs
  from the sealed manifest) and `decode`. An I/O error on the share is never a
  verdict: the block aborts and retries. Slot coverage is by grid key, so a
  slot just before b whose capture landed at or after b counts for its own
  segment (the final block judges it) while its pixels belong to the next. Duplicates keep one attempt: verified > plausible >
  lower uncertainty > earlier. Gaps are grid slots with no accepted frame;
  collector loss records never remove a sealed frame.
- **Decode.** The collector does not decode (#75). Every accepted frame is
  decoded once by the pinned FFmpeg with bit-exact IDCT/scaling; a frame FFmpeg
  reports any error for is a per-slot `decode` reject, excluded from coverage
  and posters, and never aborts the segment.
- **Blocks.** Output frames render in blocks of at most 60 once
  `last shutter end + 2 intervals + chunk_seconds + 30 s` has passed. Chunks
  sealed later are counted in `weather_pipeline_late_frames_total` and not
  integrated. Each block is an intra-only `libx264rgb` QP 12 Matroska file
  plus JSON metadata (per-frame coverage and hash, slot verdicts, inputs, and
  the FFmpeg build and image that rendered it; a mezzanine resumed across an
  upgrade lists every build in `mezzanine_builds`), fsynced and renamed. Decoded frames do not depend on block size or restarts.

## Finalize and publish

When the last block is durable the pipeline sets `mezzanine_durable_at`,
moves the segment to `processing` and, in one FFmpeg run, concatenates the
blocks and encodes `native` (source geometry, CRF 20), `web` (≤1920 wide, CRF
21) and `mobile` (≤1280 wide, CRF 23): H.264 High, yuv420p, BT.709 tags, even
dimensions, no upscaling, `setsar=1`, CFR 60, exactly N frames, no audio,
`+faststart`, track and movie timescales 60000. Validation parses the MP4
(both timescales, N samples of 1000 ticks, total N×1000, single edit, colour
tags, geometry, faststart) and fully decodes every frame at `k/60`; all three
must share one time map. Posters are the observed frame nearest the middle,
decoded from the validated native rendition, as JPEG and WebP at 1920, 960
and 480 wide (roles `poster`, `poster_960`, `poster_480`, `poster_webp`,
`poster_webp960`, `poster_webp480`); a poster is never a slate (known exactly
from the block metadata).

The sidecar carries the exact time map, slot and per-frame exposure coverage,
gap ranges, rendition geometry, provenance (recipe, FFmpeg build, image digest,
input-manifest hash) and a `weather` block: #14's on-read aggregates over
`[a, b)` (`summary.py`; temperature, humidity, wind, gust, direction, rain,
pressure, irradiance, UV, lightning). A failed telemetry read is retried; only
after an hour past the boundary does the sidecar record `weather: null`. It
never names a frame hash, camera address, coordinate or credential.
`public_not_before = max(b, latest capture upper bound) + 630 s`.

`result.json` (RenderResult, sidecar, file list) is written last, after every
output validated, and contains no wall-clock time. A restart reuses it and
publishes with the expected revision recorded in it, so a kill between
validation and publish, between staging and commit, or between publish and the
segment update, still commits exactly one publication. If the catalog moved
under it: a deadline placeholder is superseded (the new expected revision is
written to `result.json` first); any other content is terminal for that
result (`unavailable`, reason `revision_conflict`). A failing segment backs off
from 10 s doubling to 30 min, so it never starves the camera's live segment.

## Staging layout and cleanup preconditions (#19)

```text
/staging/sealed/<camera>/<UTC hour>/<writer>.tar      collectors; read-only here
/staging/render/<camera>/<date>_<kind>_r<rev>-<recipe>/
  blocks/NNNNN.json, NNNNN.mkv, mezzanine.ffconcat     durable mezzanine
  out/{native,web,mobile}.mp4, poster*.{jpg,webp}      validated outputs
  result.json                                          prepared publication
/staging/previews/<camera>/<generation>-<variant>.jpg delayed previews (#39), about 1 h
```

The renderer deletes nothing; the preview producer prunes only its own `previews/<camera>/`
([previews](media.md#previews)). Staging cleanup (#19) runs in the same pod between loops,
ahead of rendering under pressure ([media operations](media.md#staging-cleanup-and-pressure));
it relies on:

- `segment.mezzanine_through` is a slot key: the segment will never read
  another frame whose slot is earlier. The pipeline advances it after each
  durable block to the next block's window start minus the largest lag of a
  capture behind its slot (half a cadence plus the 5 s capture deadline), and
  to the segment end once the mezzanine is complete. A sealed chunk's video
  duty is done once its slot window ends at or before `mezzanine_through` of
  every current segment whose read range `[starts_at − lag, ends_at)` its
  window overlaps, so staging holds only each segment's unrendered tail. The
  keogram and preview conditions of P3 still apply, and every overlapping hour slot needs an
  active `archival_still` row: its original archived and read back, or recorded missing for
  lack of input (`no_capture`, `no_eligible_capture`; `selection_expired` keeps the chunk);
- a render directory is deletable once its segment has `finalized_at` set and
  its renditions are committed (or its result is `unavailable`). An unfinalized
  directory whose segment has a release point is never deleted: such a segment
  stays active past `LATE_RENDER` until the renderer completes it from what is
  still on staging (gaps become slates) and publishes it, superseding the
  deadline placeholder. A segment the pipeline never started drops out after
  12 h, and its directory (if any) goes 13 h after `ends_at`;
- `.tmp-*` files belong to an in-progress write; the pipeline removes its own
  on restart.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `WEATHER_STAGING` | `/staging` | Staging share mount |
| `WEATHER_PIPELINE_GEOMETRY` | `4096x1856` | Commissioned frame geometry |
| `WEATHER_PIPELINE_CADENCE_MS` / `_PHASE_MS` | 2000 (0.5 Hz, #4) / collector default | Capture grid. Change it in the same commit as `capture.json`'s `cadence_ms` (a new capture epoch); a mismatch makes every frame a `settings` reject (`weather_pipeline_slot_rejects_total{reason="settings"}`) |
| `WEATHER_PIPELINE_SHUTTER_DEGREES` | 180 | 180, 270 or 360 |
| `WEATHER_PIPELINE_BLOCK_FRAMES` | 60 | 1–60; resources only |
| `WEATHER_CAPTURE_CHUNK_SECONDS` | 60 | Collector chunk length |
| `WEATHER_IMAGE_DIGEST` | `unpinned` | Recorded in every RenderResult; #49 sets it to the app pin |
| `WEATHER_FFMPEG` | `ffmpeg` on PATH | Tests only |
| `WEATHER_PREVIEW_SKEW_SECONDS` | 30 | Preview skew margin, 30–600 ([clock offset](media.md#clock-offset)) |
| `WEATHER_CLEANUP_*` | dry run | Staging cleanup; see [media operations](media.md#staging-cleanup-and-pressure) |

Database (`weather-pg-worker`) and Garage (`weather-media-s3-writer` and
`weather-originals-s3-writer`, projected to `/var/run/weather-garage/<bucket>/`) credentials
are mounted files. The pipeline holds the only originals key; it does not start without it.

## Deploying (#49)

`deploy/k8s/pipeline/deployments.yaml` holds `pipeline-skycam-east`,
`pipeline-skycam-west` and `pipeline-frontyard-1` at `replicas: 0`, outside
the root kustomization: non-root, read-only root, tokenless, 1 CPU/2 GiB
requested and 4 CPU/8 GiB limits each, sync wave 2, with the staging PVC, the
worker DB role and the media writer key. Their NetworkPolicy
(`network-policy.yaml`) allows DNS, CNPG and Garage only. Enable both by
adding `pipeline` to the root kustomization and setting replicas to 1 after
the cameras, `weather-staging` PVC and both bucket writer keys exist. Each pod mounts only its camera's
`sealed/`, `tmp/`, `render/` and `previews/` subPaths of the share; create `render/<camera>`
and `previews/<camera>` owned
by UID 1025 / GID 100 first (from a pod running as that identity), or the kubelet creates
it as root.

## Throughput check

`deploy/k8s/pipeline/bench-job.yaml` runs `weather pipeline bench` in
`weather` as a disposable Job (no Secret, no network): per camera, in parallel,
it times the streaming renderer over 600 s of synthetic 4096×1856 frames at the
commissioned 0.5 Hz (`--cadence-ms 1000` for 1 Hz)
(target ≥ 3× real time) and a 1,200-frame finalize extrapolated to 4,200
frames with three cameras at once (target ≤ 2 h), within the three pipelines'
combined limits. Set the image digest under test and the expiry date, apply,
read the three JSON lines from the log (`"pass": true`), record them in the
issue and delete the Job. Never run it on a workstation.

## Metrics

| Metric | Meaning and action |
|---|---|
| `weather_pipeline_output_frames_total{camera,result}` | Rendered vs gap (slate) frames |
| `weather_pipeline_slot_rejects_total{camera,reason}` | Per-slot rejects; `decode` means FFmpeg refused a sealed JPEG; `settings` means the pipeline's cadence/phase differ from `capture.json` |
| `weather_pipeline_late_frames_total{camera}` | Frames sealed after their block; check collector seal latency |
| `weather_pipeline_render_lag_seconds{camera}` | Wall time minus the mezzanine's source time |
| `weather_pipeline_slot_coverage_ratio{camera}` | Last finalized segment's slot coverage |
| `weather_pipeline_segments_total{camera,state}` | Finalized segments by state |
| `weather_pipeline_publish_latency_seconds{camera}` | Boundary to commit; SLO 1 is ≤ 2 h for 95% |
| `weather_pipeline_failures_total{camera,stage}` | `render`, `prepare`, `publish`, `catalog`, `loop`; the segment retries every 10 s |
| `weather_schedule_failures_total{reason}`, `weather_schedule_site_configured`, `weather_schedule_anchored_total` | Schedule freezing |
| `weather_segments_deadline_missed_total{camera}` | Segments still unpublished at boundary + 2 h |

Alert rules belong to monitoring-stack at #49. Logs carry fixed stage names
and exception types only, never paths, payloads or credentials.
