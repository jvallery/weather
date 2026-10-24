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
| `weather-originals` | `original` | pipeline (originals key) | none online; custody only |

Object keys are `<class>/<sha256>.<ext>`, where the hash is computed locally
from the bytes, never taken from an ETag. Public URLs are same-origin
`/media/{asset_id}`; no bucket name or key leaves the service.

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
| `public_api.assets` | Security barrier: public classes in `weather-media`, available, not withdrawn, past embargo |
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
`keogram_state: withdrawn`. The bytes remain in Garage; hard purge is #19.
Withdrawing again is a no-op. There is no un-withdraw: the database refuses it.

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
  `private_reference`, `missing_object` (absent) or `object_mismatch` (present
  with other bytes). Orphans are reported, never deleted here. #19's GC may
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
