# Media publication

`app/weather/publish/` turns validated product files into immutable,
content-addressed Garage objects and commits them to the catalog in one CNPG
transaction (#18). Migration `0003_publication` owns the tables and views
below. Live use starts with the pipeline Deployments (#15) and the bucket keys
(storage-infra#1107); slice 2 launches in #49.

## Buckets, keys and identities

| Bucket | Classes | Writers | Readers |
|---|---|---|---|
| `weather-media` | `video`, `poster`, `keogram`, `still`, `sidecar` | pipeline (media key) | api (read-only key, #20) |
| `weather-originals` | `original` | pipeline (`weather-originals-s3-writer`, its only key) | none online; custody only |

Object keys are `<class>/<sha256>.<ext>`, where the hash is computed locally
from the bytes, never taken from an ETag. Public URLs are same-origin
`/media/{asset_id}` (see [Serve](#serve)); no bucket name or key leaves the service.

Each bucket has its own key. The pipeline reads them from
`$GARAGE_CREDENTIAL_DIRECTORY/<bucket>/access_key_id` and `secret_access_key`
(default `/var/run/weather-garage`). `GARAGE_ENDPOINT` defaults to
`https://s3-hdd.vallery.net` and `GARAGE_REGION` to `garage`. Never print or
log a key, request header or signed URL.

## How a publish works

1. **Hash locally.** Each file is hashed; the hash names the object.
2. **Intent.** An `upload_intent` row records the whole manifest with a new
   fencing token from `weather.publication_fence`, the expected product
   revision and a deadline. The deadline is immutable (trigger), so a retry
   can never extend it, and at most two days after the intent (CHECK).
3. **Conditional create.** HEAD each key. If the key exists, its size and
   stored `x-amz-meta-sha256` must match, or the publish fails with
   `ObjectConflict` and the object is never overwritten. Otherwise PUT with
   `If-None-Match: *` and the signed payload hash, so the store rejects bytes
   that don't match. Garage v2.3.0 ignores `If-None-Match` (fleet CI shows a
   second PUT succeeding), so a race between two writers can only rewrite a key
   with the same bytes: the signed hash must equal the key's hash. Files over 64 MiB use 16 MiB multipart parts, hashed as
   they stream; the upload completes with `If-None-Match: *` only if the whole
   matches the key. Any failure, cancellation, mismatch or lost race aborts it.
4. **Readback.** HEAD (size and stored hash), then GET and hash every byte.
5. **Commit**, in one transaction under a per-product advisory lock:
   - the intent must be pending, the newest fence for the product and before
     its deadline;
   - the product must not be withdrawn (no `product_tombstone`), nor any of
     the bytes;
   - a sidecar must not contain the sha256 of any archived original;
   - the product revision must equal the expected revision (CAS);
   - then it writes the `media_asset` rows, a `publication` revision with its
     `publication_asset` roles, the `video` state for videos, marks the intent
     committed, and appends an outbox `media.published` event that is not due
     before the product's `public_not_before`, so consumers never see an
     embargoed product.

A retry after an unacknowledged commit (same objects, state and timing) is a
no-op that returns the committed revision. A commit from an older attempt
fails with `StaleFence`. A withdrawn product, or withdrawn bytes, fail with
`Withdrawn`, whether retried or re-rendered.

A video can also be committed with no assets at all, only when its state is
`unavailable`: the renderer's deadline path (#15) records a camera that missed
its boundary this way, so the catalog says so without uploading anything.

Stills and keograms publish without a video. Originals commit only to a still,
under the `original` role, to the private `weather-originals` bucket with no
embargo and no publication time. The database enforces this as well as the
code.

A sidecar revision (`Publisher.publish_sidecar`) keeps every other role and
replaces only the sidecar, so revision N+1 serves the same media hashes.
Sidecars are checked before upload: no `bucket`, `object_key`, `config_hash`,
`original*` field, originals bucket name or originals key. At commit, a
sidecar containing the sha256 of an already archived original is refused
(`private_reference`); originals are archived within 30 minutes of their hour,
before the segment's video publishes. Sidecar bytes are also kept inline in
`media_asset.sidecar`.

## Catalog and privacy rules

| Relation | Purpose |
|---|---|
| `upload_intent` | One row per publish attempt: manifest, fence, expected revision, immutable deadline, `pending` / `committed` / `orphan` |
| `media_asset` | One row per object. CHECKs: `original_private` (originals only, and only originals, are private in `weather-originals`), `embargo` (every public class has `public_not_before` and `published_at`; originals have neither), `content_address`, inline sidecar hash |
| `publication`, `publication_asset` | Append-only revision log of every product (video, keogram, still) with its roles; `publication.public_not_before >= source_end + 630 s` |
| `video` | Current state of each (camera, cycle date, segment); `public_not_before >= ends_at + 630 s`; withdrawal tombstone |
| `withdrawal`, `product_tombstone` | Append-only audit of withdrawals, and the withdrawn products every later commit is checked against |
| `public_api.assets` | Security barrier: public classes in `weather-media`, available, not withdrawn, past embargo; a still image only through an active, untombstoned selection (0009) |
| `public_api.videos` | Security barrier: past `public_not_before` and `ends_at + 630 s`; renditions only when all four are public; a withdrawn video lists with no media |

The pipeline uses the `weather_worker` role, with UPDATE only on the columns
the code writes (asset state and withdrawal; intent state; the video's current
revision). A trigger makes withdrawal one-way, fixes an asset's embargo and
lets a video's embargo only move later. The API reads only the two views.

## Withdraw

```sh
kubectl --context vallery -n weather exec deploy/scheduler -- \
  weather media withdraw <video_id|asset_id> --reason "<why>" [--actor <name>]
```

One transaction writes the tombstones, a `withdrawal` audit row and a
`media.withdrawn` outbox event:

- **Video:** the video and its segment's keogram are tombstoned, so neither
  publishes again, re-rendered or not. Every asset either ever published is
  withdrawn, except bytes another product also publishes (assets are
  content-addressed, so byte-identical files share one row; those stay public
  through the other product).
- **Asset:** that file is withdrawn wherever it is used, and any keogram or
  still that published it is tombstoned. A video asset withdrawn this way
  stops only that file; withdraw the video to stop the video.

The API then lists the video with `withdrawn: true`, no media URLs and
`keogram_state: withdrawn`. The bytes remain in Garage; to remove them see [hard purge](#hard-purge).
Withdrawing again is a no-op. There is no un-withdraw: the database refuses it.

## Serve

The `api` streams published media itself (#20); code in `app/weather/media/`
(`origin.py` is shared with #39's `weather-preview`). Contract:
[api-contract](../contracts/api-contract.md#media-origin-implemented-20).

- **Routes.** `/media/{asset_id}` serves any row of `public_api.assets`;
  `/downloads/{video_id}/native` serves a listed video's native rendition as
  an attachment named `weather-<camera>-<cycle-date>-<segment>-native.mp4`.
  `deploy/k8s/ingress.yaml` sends both prefixes to `api` with the per-visitor
  rate limit and `weather-media-in-flight` (6 open requests per visitor).
- **Probe target (#49).** `/media/latest/{video,poster,keogram,still}` serves
  the newest asset of that kind (the web rendition for `video`) whose source
  ended in the last 14 days (2 days for stills), chosen from `public_api.videos` or
  `public_api.stills`, so every embargo and withdrawal rule applies. It is
  re-resolved on every request, `no-store`, HEAD-able and ranged like any
  asset, and answers 404 while nothing qualifies. It never reaches a camera.
  Blackbox probes it as `component: media` (monitoring-stack `weather-probes`).
- **Breach detector.** Just before a response's first body bytes are sent, if
  they are less than 600 s past the capture upper bound (by the database clock
  that authorized them), `weather_embargo_served_before_total{product}`
  increments. That should be impossible and pages. It covers previews and
  `/media/latest/` only. `/media/{asset_id}` is not checked yet, because
  `public_api.assets` exposes no capture bound to the api role.
- **Eligibility.** Checked on every GET and HEAD against the primary with the
  database clock, never cached. A withdrawal (above) answers 404 from the
  next request. Every denial is the same 404 with no ETag.
- **Bytes.** The object key is rebuilt from the row's content address and read
  from `weather-media` with the read-only key, Secret `weather-media-s3-reader`,
  mounted optionally at `/var/run/weather-garage/weather-media`. Egress is
  NetworkPolicy `api-garage-egress`. Without the Secret the api still starts,
  HEAD answers from the catalog and GET answers 503; restart `deploy/api` after
  the Secret appears.
- **Limits.** 32 streams per pod (503 before any body beyond that), one 64 KiB
  upstream chunk in flight per stream, a client that stops reading for 60 s is
  cut off, and a disconnect closes the Garage read at once. A Garage error
  before the headers is 503 `Retry-After: 5`; after them the connection is cut
  short of its Content-Length, and uvicorn logs one line, "ASGI callable
  returned without completing response." (also for stalled clients).

```sh
id=<asset_id>
curl -sI "https://weather.vallery.net/media/$id" \
  | grep -iE '^(HTTP|etag|content-(type|length)|cache-control|accept-ranges)'
curl -s -o /dev/null -w '%{http_code} %{size_download}\n' -H 'Range: bytes=0-1' \
  "https://weather.vallery.net/media/$id"
kubectl --context vallery -n weather exec deploy/api -- python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9000/metrics').read().decode())" \
  | grep -E '^weather_media_'
```

| Metric | Meaning and action |
|---|---|
| `weather_media_requests_total{route,method,status}` | Responses by route (`media`, `download`, `latest`) |
| `weather_media_streams{route}` | Requests in progress; near 32 per pod means saturation (503s) |
| `weather_media_bytes_total{route}` | Body bytes written |
| `weather_media_denials_total{route,reason}` | `ineligible` (unknown, embargoed, withdrawn, original), `path`, `method`, `range` (400), `unsatisfiable` (416), `overload` (503) |
| `weather_media_upstream_errors_total{route,source,phase}` | `catalog` or `store` failures before (`open`, a 503) or after (`stream`, a cut connection) the headers. Check `weather-pg`, then Garage and `api-garage-egress` |
| `weather_media_cancels_total{route,reason}` | `disconnect` or `stalled` clients |
| `weather_media_ttfb_seconds{route}` | Request start to first body byte |
| `weather_embargo_served_before_total{product}` | Bytes served less than 600 s after capture, from previews and `/media/latest/` (not `/media/{asset_id}`); always 0. Any increment is a privacy breach: withdraw the product, then find which check let it through |

| Symptom | Likely cause | Action |
|---|---|---|
| Every GET 503, HEAD 200 | Reader Secret absent, or Garage unreachable | `kubectl -n weather get secret weather-media-s3-reader`; check `store` upstream errors and the traefik-apps route to `s3-hdd.vallery.net` |
| GET and HEAD 503 | `weather-pg` unreachable or slow | `kubectl cnpg status weather-pg -n weather` |
| 404 for a listed asset | Embargo not yet over, or withdrawn | Compare `public_not_before` and `withdrawn` in `weather.media_asset` |

## Previews

Delayed current previews (#39), [policy](../contracts/preview-policy-contract.md). Not in
Garage or custody: disposable files on the staging share plus `camera_preview` pointer rows
(migration `0007_previews`).

- **Producer.** Each `pipeline-<camera>` runs `app/weather/preview/producer.py` beside its
  renderer. Every 15 s it lists `sealed/<camera>/` for the current and previous UTC hour (never
  `tmp/`), reads new sealed chunks once, and in each UTC minute publishes the newest frame
  whose `capture_upper_bound + 600 s + skew_margin` has passed, if it is newer than the last
  one. Repeats of the same camera bytes (`duplicate_hash`, or the last published source hash)
  are skipped, so a frozen camera's preview goes stale, then `too_old`. Derivatives `large` (≤1280 px) and `small` (≤480 px) are decoded, resized and
  re-encoded with no EXIF, XMP, GPS, ICC or comment, written tmp → fsync → rename to
  `previews/<camera>/<generation>-<variant>.jpg`, then both rows commit with
  `first_published_at` from the database clock. Rows are deleted at `expires_at`
  (capture + 3,600 s), files about 65 minutes after their generation.
- **Serving.** Deployment `weather-preview` (`weather preview`, same image) is the only
  public-path pod that mounts staging, and only `previews/` read-only at `/previews`. It reads
  `public_api.previews` as `weather_api`. IngressRoute `weather` sends
  `/api/v1/cameras/<camera>/preview` and `/media/previews/` to it (priority 110). Bytes reuse
  `origin.py`: every denial is the identical 404, responses are `no-store`, no 304.
  `api` has no NFS volume and never imports the preview routes, so a NAS outage cannot touch
  `/api/v1/weather/current`.
- **Health.** `/healthz` touches neither the database nor the share; the pod stays ready and
  answers `unavailable` honestly. File reads run on 4 threads with a 2 s deadline; once all
  are stuck on a stalled mount, requests answer at once.
- **First deploy.** The `previews` subPath must exist before the pod starts (kubelet would
  otherwise create it root-owned). It was created as UID 1025 / GID 100, mode 2775, with the
  first rollout; the producer creates the per-camera directories.

| Symptom | Likely cause | Action |
|---|---|---|
| Pointer `no_preview` for every camera | Pipelines not running (#49), no sealed input, or every frame's timing unknown | `kubectl -n weather get deploy -l app.kubernetes.io/component=pipeline`; `weather_preview_failures_total`; collector metrics |
| Pointer `too_old` | No new eligible frame for 30 min: collectors paused, NAS writes refused, or clock offset | [capture runbook](capture.md); [clock offset](#clock-offset) |
| Pointer `media_unavailable` | `previews/` missing, stalled, or a file differs from its row | `weather_preview_file_failures_total{reason}`; check the NAS (storage-infra); the next generation recovers by itself |
| 503 on the pointer | `weather-pg` unreachable | `kubectl cnpg status weather-pg -n weather` |
| `weather_preview_failures_total{stage="catalog"}` rising | Database refuses rows: usually its clock is behind the collectors' | [clock offset](#clock-offset) |

**Withdraw** a preview immediately (it would expire within the hour anyway):
`UPDATE weather.camera_preview SET withdrawn_at = clock_timestamp() WHERE camera_id = '<camera>'
AND generation >= <first>` as the migrator through `kubectl cnpg psql`. The view denies it on
the next request; pointer and bytes answer `no_preview` and 404.

### Clock offset

The embargo depends on two clocks: the collector's (capture bounds) and the database's
(`first_published_at`, request-time eligibility). Node clocks are owned by
proxmox-infrastructure and alerted by `WeatherNodeClockOffset` (monitoring-stack#1072); there is
no clock adapter (P14).

- `skew_margin` is 30 s by default (`WEATHER_PREVIEW_SKEW_SECONDS` on the pipelines, ≥ 30). The
  database floor is fixed at 630 s; raising the margin only delays publication.
- **Collector clock ahead** of true time: capture bounds are later than reality, so frames
  publish later than needed. Safe; previews age toward stale.
- **Collector clock behind** true time: capture bounds are earlier than reality. The 30 s
  margin absorbs that much, so 600 s of real delay still holds. Beyond it the real delay
  shortens by the excess, because both the producer and the database judge eligibility from
  the collector's bounds. When `WeatherNodeClockOffset` fires for a collector node, raise
  `WEATHER_PREVIEW_SKEW_SECONDS` to at least the offset (Git, then Argo sync), or scale
  the pipelines to 0 until the node is fixed.
- **Database clock behind**: inserts fail the CHECK (`stage="catalog"` failures) and previews
  go `too_old`; nothing is published early.
- Future, reversed, unknown (`clock_discontinuity`) or wider-than-300 s bounds are never
  eligible.

| Metric | Meaning |
|---|---|
| `weather_preview_age_seconds{camera}` | Now minus the capture lower bound of the newest published preview (`-1` before the first); healthy about 11–12 min |
| `weather_preview_generations_total{camera}` | Generations published (about one a minute) |
| `weather_preview_failures_total{camera,stage}` | `scan`, `derive`, `catalog`, `cleanup` |
| `weather_preview_pointer_total{camera,state}` | Pointer responses by state |
| `weather_preview_embargo_violations_total{route}` | Rows the view admitted that the request-time re-check refused; always denied, so nothing is served before its embargo. A rare increment at an exact boundary is the two clock reads differing by microseconds; a steady rate means the view and `policy.py` disagree: investigate |
| `weather_preview_file_failures_total{reason}` | `missing`, `mismatch`, `timeout`, `busy`, `error` |
| `weather_media_*{route="preview"}` | The shared origin metrics for preview bytes |

Alert rules and the scrape belong to monitoring-stack at #49.

## Reconcile

```sh
weather media reconcile
```

Run it from a pod with both bucket keys and Garage egress, which today means a
pipeline pod. It is idempotent and prints counts:

- `committed`: a pending intent older than 1 hour that is still valid (its
  objects read back, it holds the newest fence, its deadline hasn't passed and
  the revision still matches) is committed through the same fenced commit.
- `orphaned`: any other stale intent is marked `orphan` with a fixed reason:
  `superseded`, `deadline`, `revision_conflict`, `withdrawn`,
  `private_reference`, `missing_object` (absent), `object_mismatch` (present
  with other bytes) or `still_selection` (a still commits only with its selection, #38). Orphans are reported, never deleted here. #19's GC may
  delete an orphan's object only after the grace period, and only when no
  `media_asset` row **and no pending intent's manifest** references the key:
  a retry can share a key with an orphaned attempt and commit it later.
- `sidecars_restored`: the current sidecar of any video or keogram that is
  missing from `weather-media` is re-uploaded from its inline catalog copy and
  read back. Every current sidecar is checked, in keyset pages.
- `multipart_aborted`: incomplete multipart uploads older than 6 hours are
  aborted in both buckets.

If Garage is unreachable, the reconciler decides nothing and retries on the
next run.

## Hourly originals and comparison stills

#38. Code: `app/weather/stills/` (policy, sanitizer, catalog) and the pipeline's hourly pass
`app/weather/pipeline/hourly.py`; schema `0009_stills`; API `/api/v1/stills`; web `/stills/`
and the day page.

- **Selection** (`nearest-half-hour/v1`). Per camera and UTC hour slot, among the hour's
  eligible captures (known timing, native geometry, commissioned settings, then a clean decode
  by the pinned FFmpeg of bytes that still match the sealed manifest), the source time nearest
  `slot start + 30 min` wins; ties go to the earlier source time, then the attempt ID. Within
  60 s is `on_target`, otherwise the hour's nearest is `off_target` with its exact offset
  (`offset_us`). No eligible capture is `missing` (`no_capture` or `no_eligible_capture`,
  with per-reason rejection counts). Only source time decides membership: a capture never
  fills another hour.
- **When.** Each pipeline loop selects up to two slots of the last 12 h, oldest first, that
  ended at least `max_lag + chunk_seconds + 60 s` ago (about 2 min) and have no active
  selection, re-reading the sealed chunks, so a restart selects the same capture. A chunk that
  fails to read, an FFmpeg timeout or a decode that exits cleanly but reports errors defers the
  slot (only a failed decode is a persisted `decode` reject). A slot still unselected after
  12 h is recorded `missing`/`selection_expired` (while it is under 48 h old); older unselected
  slots the API reports as `missing`/`not_selected`.
- **Archive.** One still product (`<camera>`, `YYYY-MM-DDTHH:00Z`) through the publisher:
  role `original` is the TAR member's exact bytes (EXIF included) in private
  `weather-originals`, roles `still`, `still_960`, `still_480`, `still_webp`, `still_webp960`,
  `still_webp480` are the sanitized derivatives in `weather-media`. The `archival_still` row
  (source chunk and member, source time and capture upper bound, segment and solar cycle,
  size, SHA-256, geometry, policy, retain-until date) commits in the same transaction as the
  publication. A crash before that commit leaves only a pending intent and content-addressed
  objects; the reconciler never commits a still intent bare (it orphans it, `superseded` or
  `still_selection`), and the rerun commits exactly one active selection. A missing slot is a
  database-only row.
- **Dusk.** The winner binds to the current #15 segment that contains its source time, so an
  hour straddling civil twilight may archive a daytime or an overnight capture. No segment yet
  (schedule not frozen) retries (`stage="bind"`).
- **Derivatives** (`sanitized-still/v1`, mask policy `none/v1`): re-encoded from the decoded
  pixels, full view, JPEG and WebP at 1920, 960 and 480 px wide (never upscaled), the poster
  encoder settings. Each is scanned before upload: any EXIF, XMP, IPTC, ICC, comment, GPS or
  unknown segment refuses the still.
- **Embargo.** `public_not_before = capture upper bound + 630 s` on the publication and every
  derivative; `public_api.stills` also requires `source_ts + 630 s` and `capture_upper + 630 s`
  to have passed. A late fallback captured in an hour's last 10 minutes appears only then.
- **Corrections.** `weather.stills.catalog.record` with a different outcome retires the active
  revision (`active=false`, `retired_at`) and its derivatives (`retired`) and adds the next
  revision; retirement is one-way (trigger). The retired original stays archived.
- **Retention.** Five years (`retain_until`, `RETENTION_YEARS`), independent of video. Video
  expiry or withdrawal never touches a still: no still FK points at a video, and a video
  withdrawal withdraws only video and keogram assets. No deletion job exists (#79).
- **Withdraw.** `weather media withdraw <asset_id>` on a derivative tombstones that hour's still
  product: the API lists the slot as `withdrawn` with no images, and it is never republished.
- **Media origin gate.** `0009_stills` redefines `public_api.assets` so a `still` image is
  served only while its derivative row is `published`, its selection is active and its still
  is not tombstoned. Withdrawing one image therefore removes all six, and a correction removes
  the retired images, from the next request on.

| Metric | Meaning and action |
|---|---|
| `weather_stills_slots_total{camera,state}` | Slots finalized as `on_target`, `off_target` or `missing` |
| `weather_stills_archived_total{camera}` | Originals archived, read back and committed with their derivatives |
| `weather_stills_pending_slots{camera}` | Closed slots of the last 12 h without a selection; expected slots = finalized + pending |
| `weather_stills_oldest_pending_seconds{camera}` | Above 30 min: check `weather_stills_failures_total` and the pipeline log |
| `weather_stills_failures_total{camera,stage}` | `select` (unreadable chunk, decode), `bind` (no segment), `archive` (Garage, commit), `catalog` (database); the slot retries with backoff |

```sql
-- As weather_worker: one camera's selections for a calendar day.
SELECT slot_starts_at,revision,state,reason,offset_us,segment_kind FROM weather.archival_still
 WHERE camera_id='skycam_east' AND active AND local_date='2026-11-01' ORDER BY slot_starts_at;
```

```sh
curl -s 'https://weather.vallery.net/api/v1/stills?date=2026-11-01&camera=skycam_east' \
  | jq '.items[] | {slot_id, local_label, state, reason, offset_seconds, images: (.images|length)}'
```

## Staging cleanup and pressure

`app/weather/scheduler/cleanup.py` (#19) runs inside each `pipeline-<camera>` pod, the only
role besides the collectors that mounts staging (as UID 1025 / GID 100, the share owner), and
each pod mounts only its own camera's `sealed/`, `tmp/`, `render/` and `previews/` subPaths. The
scheduler and the api never mount NFS (C17). Each pass works on its own camera only, holds a
per-camera advisory lock, and takes every age from the database clock.

**What may go.** Catalog rows decide (P3). A sealed chunk is deleted only when every
condition holds; a kept chunk reports the first unmet one:

| Condition | Holds when | Wired by |
|---|---|---|
| `config` | The chunk header's cadence, phase and geometry equal the pipeline's. A `capture.json` mismatch makes every frame a `settings` reject, and the renderer's slates must never release the input | here |
| `mezzanine` | Every current segment whose read range `[starts_at − lag, ends_at)` overlaps the chunk has `mezzanine_through` (a slot key) at or past the chunk's slot-window end, or a complete mezzanine, and the frozen schedule covers the range. A dusk-spanning chunk waits for both segments | here |
| `keogram` | Its keogram bins are committed | #37 |
| `hourly` | Every UTC hour slot the chunk's frames can be selected for has an active `archival_still`: archived with its still publication committed (read back first) and its original asset still `available`, or `missing` for lack of input (`no_capture`, `no_eligible_capture`). `selection_expired` (no selection ran for 12 h, e.g. a Garage or pipeline outage) keeps the chunk, and the 48 h pinned alert fires | here, against #38's `archival_still` |
| `preview` | Its newest capture is more than 1,800 s plus one refresh (`WEATHER_CLEANUP_PREVIEW_REFRESH_SECONDS`, 60) old | here (#39's window) |

`keogram` is unmet until #37 registers its query in `cleanup.CONDITION_QUERIES`: an async
function `(connection, camera, chunks, lag)` returning the ids of the given `ChunkRow`s whose
condition holds, read in the same connection as the segment rows, plus a row in the table
test (`hourly_finalized` is the model). Until then, `WEATHER_CLEANUP_KEOGRAM=vacuous` waives
it; set it only while #37 is not deployed, in the reviewed change that enables cleanup. A
vacuous mode for a registered query fails start-up, and the rule evaluates a wired condition
regardless. `config`, `mezzanine`, `hourly` and `preview` cannot be waived.

Released input is gone for good, so the release point is tied to the blocks on disk:
`segment.mezzanine_recipe` records the recipe whose durable blocks `mezzanine_through`
describes, a renderer of another recipe cannot advance it, and a renderer whose durable blocks
(listed with errors raised, never silently empty) are behind it refuses to render and counts
`weather_pipeline_release_incidents_total`. A durable block is never replaced. Change the
recipe or correct a segment only before the segment starts; a mid-segment change is an
incident for that segment (the deadline sweep marks it unavailable), never a re-render of
deleted input.

Also removed, with the same audit row: other incarnations' `tmp/<camera>/` files whose
slot-window end and mtime are both older than `WEATHER_CLEANUP_TMP_HOURS` (6; a live writer
seals within 300 s and appends every cadence), and `render/<camera>/<segment>-<recipe>/`
directories once their segment is finalized with its three renditions committed (the publisher
read every byte back first) or finalized `unavailable` (the pipeline's terminal verdict:
insufficient coverage, withdrawn, revision conflict). An unfinalized directory whose segment
has a release point (`mezzanine_through`) is never deleted, however late: its blocks may be
the only copy of input cleanup already removed. Such a segment stays active in the pipeline
past `LATE_RENDER` until the renderer completes it from what is still on staging (gaps become
slates) and publishes it as a partial or unavailable video over the deadline placeholder;
then the normal rules release its chunks and directory. A segment the pipeline never started
leaves at most an empty directory, removed 13 h after its end (12 h plus an hour for the pod's
clock). Empty sealed hour directories go when both the hour and the directory are older than
the tmp age.

Mezzanine bytes per released input byte are not yet measured; the throughput Job
([pipeline](pipeline.md#throughput-check)) is where to record them. Watch
`weather_staging_bytes{class="render"}` against `{class="sealed"}` and
`weather_staging_pinned_bytes` meanwhile.

**How.** Each deletion is two-phase: a `staging_deletion` row (name, manifest sha256, size,
reason) commits first, then the unlink, then `unlinked_at`. Before acting, the name is
re-validated against its class pattern and camera (also a CHECK in the table) and no path
component may be a symlink; an unsafe row is refused and counted (`unsafe_target`). The next pass finishes any
deletion a crash left; an already-gone file is a no-op. A chunk is re-identified (manifest
sha256 and size) before the decision and again before the unlink, and is never deleted if
either differs. Files without a catalog row (unreadable, invalid, another camera's, unknown
names) are never touched; `weather_staging_unindexed_files` counts them. `staging_chunk` rows
stay as metadata (`deleted`, or `missing` when a file vanished without a decision). An I/O
error on the soft mount aborts the pass (nothing is ever inferred from a failed listing) and
the next pass retries.

**Configuration** (pipeline Deployments):

| Variable | Default | Meaning |
|---|---|---|
| `WEATHER_CLEANUP_ENABLED` | `false` | Delete; otherwise every pass is a dry run that inventories, evaluates and exports metrics |
| `WEATHER_CLEANUP_INTERVAL_SECONDS` | 300 | Pass interval at normal pressure |
| `WEATHER_CLEANUP_TMP_HOURS` | 6 | Age of another incarnation's tmp file before removal |
| `WEATHER_CLEANUP_KEOGRAM`, `_HOURLY` | `required` | `vacuous` waives an unwired (undeployed) feature's condition; refused once its query is registered (hourly already is) |
| `WEATHER_CLEANUP_BATCH` | 2000 | New chunks inventoried per pass |

**Watermarks** (D16, `scheduler/pressure.py`): `statvfs` on `/staging/sealed/<camera>` (the
share's quota), the same ratio the collectors compute.

| Level | At | Effect |
|---|---|---|
| alert | ≥ 70 % | Metrics only |
| urgent | ≥ 85 % | Cleanup runs ahead of rendering at most once a minute (instead of every 5 minutes); render-directory sizes are cached by directory mtime and their segment states read in one query; `optional_allowed()` is false, so no optional re-render or reanalysis may start (none exists yet) |
| stop | ≥ 90 % | The collectors stop writing JPEG payloads on their own statvfs check and record `staging_full` gaps ([capture](capture.md)). The pipeline keeps rendering, which is what releases input |
| render stop | ≥ 97 % | The pipeline adds no mezzanine blocks (`weather_pipeline_pressure_paused_total`) but still finalizes complete mezzanines, whose directories cleanup then frees; it resumes below 97 % |
| resume | < 75 % | Urgent and stop hold until occupancy is below 75 % and the last cleanup pass succeeded |

The stop level holds whether or not any cleanup condition is wired or cleanup is enabled:
the collectors' check is their own, and the pipeline's pause reads only statvfs, so the share
cannot fill past 97 % by more than in-flight writes. With `keogram` still
unwired and required, no chunk is released, so input then piles up to the stop mark and capture
pauses there (gaps, no loss of sealed input) until those features land or Jason waives them.
Pressure changes only what runs first. It never relaxes a condition, so no pinned input is
ever deleted to make room. Unresolved input older than 48 h is an incident
(`WeatherStagingChunkPinned`), never authority to delete.

**Check and run by hand.** Inside the camera's pipeline pod:

```sh
kubectl --context vallery -n weather exec deploy/pipeline-skycam-east -- \
  weather staging cleanup --camera skycam_east --dry-run
```

It prints counts: `inventoried`, `eligible`, `pinned_<condition>`, `unindexed`, `tmp_stale`,
`render_eligible`, and `deleted_*` when enabled. Without `--dry-run` it deletes only if
`WEATHER_CLEANUP_ENABLED` is true. Never delete sealed input by hand: a chunk pinned by
`mezzanine` is renderer input. When `unindexed` grows, list the files in the camera's sealed
directory and investigate (an invalid chunk is kept as evidence); remove one only on Jason's
instruction.

| Metric | Meaning and action |
|---|---|
| `weather_staging_ratio{camera}`, `weather_staging_watermark_level{camera}` | Occupancy and level (0–3) seen by each pipeline; -1 when statvfs fails |
| `weather_staging_bytes{camera,class}` | `sealed` (inventoried), `tmp`, `render` bytes |
| `weather_staging_chunks{camera,condition}` | Present chunks: `eligible` or the first unmet condition |
| `weather_staging_oldest_eligible_chunk_seconds{camera}` | Above 1 h with cleanup enabled: passes are failing; check `weather_staging_cleanup_failures_total` |
| `weather_staging_oldest_pinned_chunk_seconds{camera,condition}` | Above 48 h: the named product is stuck (a stopped pipeline, a missing schedule, an unwired feature) |
| `weather_staging_pinned_bytes{camera,condition}` | Bytes kept by each condition: alert on its growth before the 90 % stop |
| `weather_staging_unindexed_files{camera}` | Sealed files no row covers; never deleted |
| `weather_staging_deletions_total{camera,class}` | `chunk`, `tmp`, `render` deletions |
| `weather_staging_cleanup_failures_total{camera,stage}` | `scan`, `identity`, `unlink`, `unsafe_target`, `statvfs`, `pass` |
| `weather_staging_cleanup_last_success_timestamp_seconds{camera}`, `weather_staging_cleanup_enabled{camera}` | Pass freshness and mode |

## Object lifecycle

`app/weather/scheduler/lifecycle.py` (#19). Every state change and deletion appends an
`object_event` row (`unavailable`, `expired`, `gc_deleted`, `purged`, with reason and actor);
`media_asset.state_changed_at` dates the change.

### Reconcile objects

```sh
weather reconcile-objects --bucket weather-media [--rate 20] [--max-missing-ratio 0.05]
```

Run it after any restore (P7), from a pod holding that bucket's key (a pipeline pod for
`weather-media`; `weather-originals` needs the originals key, pipeline role only). It HEADs
every `available` asset of the bucket in keyset pages, at most `--rate` per second, and marks
a missing or wrong-size object `unavailable`. Circuit breaker: the bucket must exist, every
row is checked before any is marked, and if more than `--max-missing-ratio` of them are
missing it marks nothing and exits 3 (`weather_reconcile_aborts_total`). That is usually a
wrong endpoint, key or bucket; rerun with a higher ratio only after confirming a real loss. The public views then hide it, and a video
missing a rendition lists as `unavailable` / `media_unavailable`. It prints `checked`,
`present`, `missing`, `marked_unavailable` and `errors`; a store error decides nothing for
that object. Rerunning is idempotent. An object restored later stays `unavailable` until a
republish of the same bytes (the catalog revives it after readback). Metrics:
`weather_reconcile_missing_objects{bucket}`, `weather_reconcile_marked_unavailable_total`.

### Retention expiry

```sh
weather media expire [--dry-run]
```

Expiry only tombstones (`state = 'expired'`), never deletes bytes. It always lists both rules'
candidates (`candidates` by `rule:class`) and applies only the enabled ones:

| Rule | Flag (default false) | Expires |
|---|---|---|
| video | `WEATHER_RETENTION_ENABLED`, `WEATHER_RETENTION_VIDEO_DAYS` (365) | Videos, posters and sidecars of video publications whose cycle date is older |
| hourly | `WEATHER_HOURLY_ARCHIVE_DELETION_ENABLED`, `WEATHER_HOURLY_ARCHIVE_RETENTION_YEARS` (5) | Stills, originals and keograms whose America/Denver calendar date is older |

An asset that an unexpired product also publishes is kept. Video expiry never touches stills,
events, `weather_day` or slot rows. Both flags stay off until #79; #38 extends the hourly rule
to its slot rows. Metric: `weather_expiry_candidates{rule}`.

### Orphan and tombstone GC

```sh
weather media gc --bucket weather-media [--dry-run]
```

GC deletes only bytes no restorable catalog state references: keys of `orphan` upload intents
resolved more than `WEATHER_GC_GRACE_DAYS` (90) ago, and `expired` assets tombstoned more than
grace ago. It keeps a key any `media_asset` row, pending or committed intent, or orphan still
inside grace names. Each deletion is fenced by a per-object-key transaction lock that the
publisher also takes when it registers an intent and when it commits: under the lock GC
re-checks the candidate and commits its `gc_deleted` row, then under the lock again deletes
only if no pending intent or live row references the key (otherwise the audited bytes stay,
`kept_after_audit`). An upload registered after the delete sees the key gone and re-uploads,
so byte-identical content may be published again (the asset revives after readback) and is a
GC candidate again from its next tombstone; purged bytes never revive. It **refuses** (exit 3)
unless
`WEATHER_RECOVERY_PITR_WINDOW_DAYS` (30, ConfigMap `weather-database-policy`) and
`WEATHER_RECOVERY_B2_VERSION_RETENTION_DAYS` (storage-infra's custody version retention) are
set and grace ≥ max(both) + 7 days. Originals need `WEATHER_HOURLY_ARCHIVE_DELETION_ENABLED`.
With `WEATHER_GC_ENABLED` false (the default) it only counts candidates. The scheduler runs it
for `weather-media` only once #36 enables it and mounts a key; metrics `weather_gc_candidates`,
`weather_gc_deletions_total`, `weather_gc_refusals_total{reason}`.

### Hard purge

Third-party privacy (P20) can override grace, **only on Jason's explicit instruction**:

1. Withdraw the video or asset first (above), so it already answers 404.
2. From a pod holding the bucket's key:

   ```sh
   weather media purge <asset_id> --bucket weather-media \
     --reason third_party_privacy --actor jason
   ```

   It sets the asset `unavailable` and appends a `purged` row with the reason and actor,
   then deletes the object; if the delete fails, rerun the same command. An original
   (private) needs no withdrawal; run it in a pipeline pod with the originals key.
   A recent original may still be inside a sealed staging chunk (`archival_still.chunk`,
   `member`) until cleanup releases it. To remove it now, from the camera's pipeline pod:
   `weather staging cleanup --camera <id> --dry-run` to see whether the chunk is already
   eligible; if not, delete `/staging/sealed/<camera>/<chunk>` by hand only after that
   segment's mezzanine is past it (`segment.mezzanine_through` ≥ the chunk's slot-window end),
   and record it in the issue. The next pass marks the row `missing`.
3. Ask storage-infra to purge the same object key from the B2 custody mirror, including its
   hidden versions; Weather keys cannot. Database backups hold only the catalog row, never
   the bytes.
4. Record the instruction, the asset id and the command result in the issue that asked for
   it.

## Failure isolation

Each camera's pipeline publishes its own products. A failure (Garage error,
readback mismatch, conflict or a refused commit) is raised for that product
only. It leaves no catalog rows and no incomplete multipart upload, and other
cameras keep publishing.

## Metrics

| Metric | Meaning and action |
|---|---|
| `weather_publish_attempts_total{product,camera}` | Publish attempts |
| `weather_publish_failures_total{product,camera,reason}` | Failed attempts. `object_conflict`: an existing object differs from its key. Investigate; never delete it to make room. `readback`: a stored object doesn't match. `store`: Garage error. `superseded`, `revision_conflict`, `withdrawn`, `deadline`, `private_reference`: refused commits |
| `weather_publish_readback_failures_total{class}` | HEAD or GET-and-hash mismatches |
| `weather_publish_objects_total{class}`, `weather_publish_bytes_total{class}` | Objects and bytes uploaded and read back |
| `weather_publish_orphans_total{reason}` | Intents the reconciler marked orphan |
| `weather_publish_reconciled_total{action}` | Reconciler commits, restored sidecars, aborted multipart uploads |
| `weather_publish_oldest_pending_seconds` | Oldest uncommitted intent, a ready but unpublished product; set by each reconcile run |

Alert rules and the scrape belong to monitoring-stack at #49.

## Network

`deploy/k8s/pipeline/network-policy.yaml` gives pipeline pods DNS, CNPG and
HTTPS to the traefik-apps pods that serve `s3-hdd.vallery.net`, plus metrics
ingress from `observability`. It is not in the root kustomization. Enable it
with the pipeline Deployments once the bucket keys exist.

## Integration tests

`scripts/ci_integration.sh` starts a loopback, single-node Garage v2.3.0 next
to the disposable PostgreSQL, with both bucket names and one test key. The
checksum-pinned binary comes from `scripts/bootstrap_ci.py`. These tests run
only in fleet CI.
