# Weather database operations

`weather-pg` is live in `weather` (commissioned 2026-10-24, #6). Argo
Application `weather` renders `deploy/k8s/database/`. The Cluster and
ObjectStore carry `Prune=false,Delete=false`. **Never delete the Cluster, its
PVCs or the namespace to redeploy.**

## Topology and identities

- Three instances on `longhorn-v1-rwo` (20Gi each), with required
  anti-affinity on `topology.vallery.net/proxmox-host` and control-plane nodes
  excluded. The first commissioning placed them on opal, oro and onyx.
  Quorum sync `ANY 1` with `synchronous_commit=on`.
- Requests 250m/1Gi and limits 4 CPU/2Gi per instance, plus the Barman sidecar
  (100m/128Mi, limit 1/512Mi). The CPU burst allowance keeps the bounded concurrent
  API aggregate reads from starving ordinary reads on the primary; requests and
  memory bounds stay unchanged. Recheck node capacity, namespace quota and actual
  SQL/HTTP latency before changing these bounds.
- Image: the zot mirror of PostgreSQL 16.15, pinned as `:16.15@sha256:…` with
  `imagePullSecrets: zot-origin-cluster-pull`. CNPG admission rejects a
  digest-only `imageName`. pgvector 0.8.6.
- Database `weather` is owned by `weather_migrator`. Managed login roles are
  `weather_ingest`, `weather_worker`, `weather_api` (40-connection limit) and
  `weather_editor`. None is superuser, createdb or createrole, and superuser
  access is disabled. Passwords live in `weather-pg-<role>` Secrets (agents
  KSOPS); never print them or put them in a URI.
- Logging is terse, with no statement or parameter logging, so private packet
  identifiers never reach PostgreSQL logs. Don't enable verbose logging on
  production.
- Clients carry `weather.vallery.net/database-role=<role>` and connect to
  `weather-pg-rw:5432` with `sslmode=verify-full` and the `weather-pg-ca` CA.
  kube-router programs a new pod's NetworkPolicy rules a few seconds after
  start, so **every client must retry its first connection** (bounded).
- Argo compares the Cluster server-side (`compare-options:
  ServerSideDiff=true`); the client-side diff reports a perpetual OutOfSync.

Role check: render `deploy/acceptance/restore/role-logins.yaml` with a unique
operation and future expiry, apply it, read the Job log (role names and
privilege booleans only), then delete the Job.

## CPU saturation and read latency

Compare the primary's CPU usage with its CFS throttled-period ratio and read-only
`pg_stat_activity` wait/age summaries. A Ready three-instance cluster proves
availability, not query latency. Check the running app digest as well as Argo's
source revision: the aggregate `work_mem` fix must be in the built app image.
Never log query parameters or enable statement logging to investigate latency.

The 4-CPU PostgreSQL caps add 9 CPU to the former three-instance limit total.
At the recovery preflight, namespace limits were 25.5/40 CPU; this change makes
them 34.5/40 without changing reservations. The commissioning/drill envelope in
the deployment runbook is historical sizing, not permission to exceed quota;
recalculate it before admitting a drill alongside these caps.

Deliver a Cluster resource change in a coordinated maintenance window: CNPG can
roll its instances, including a brief primary restart under the configured
`primaryUpdateMethod`. Keep the maintenance Lease, healthy standbys, synchronous
replication and WAL archiving; never delete a Cluster or PVC. After the intended
source is live, verify actual pod limits, replication/archive health, ordinary
and aggregate HTTP requests, then complete the stateful-change soak. Revert the
source CPU cap if the change causes harm; do not bypass admission or alert rules.

## Backups and WAL

- The Barman Cloud plugin writes base backups and WAL to
  `s3://weather-db-vallery/cnpg/weather-pg` using `weather-pg-backup-b2-writer`.
  That key cannot permanently erase versions (storage-infra#1112). The daily
  base runs at 09:15 UTC on a standby. `archive_timeout=300s`.
- **No `retentionPolicy`.** Expiry is the B2 lifecycle: hide after 33 days,
  delete hidden versions 7 days later. That covers the 30-day PITR window
  (`ConfigMap/weather-database-policy`). Don't add a retention policy: Barman's
  deletes would hide archive objects early.
- There is no keep-last floor. If base backups fail for more than 7 days, ask
  storage-infra to suspend the `cnpg/` lifecycle rule before it expires the last
  base.
- Alerts (monitoring-stack): `WeatherDbBaseBackupStale` (>26 h) and
  `WeatherDbWalArchiveStale` (>30 min). Inspect with
  `kubectl -n weather get backups.postgresql.cnpg.io,scheduledbackups,objectstores`
  (use the qualified `backups` name; other CRDs share it) and
  `pg_stat_archiver`. Never delete WAL by hand, disable archiving, or mint a
  replacement key; recover the existing identity from KSOPS custody.
- WAL floor: 288 × 16 MiB ≈ 4.5 GiB/day at idle. Re-measure
  `pg_stat_wal.wal_bytes` after Tempest ingest starts, and expand storage
  through source if needed.

### Local WAL archive (storage-infra#1253)

The WAL archiver and the daily base backup write to ObjectStore `weather-pg-local`
(bucket `weather-pg` on the in-cluster availability Garage, retention 7 days), so
archiving keeps working with both WANs down and pg_wal never fills. The offsite copy is
storage-infra's daily Longhorn B2 backup of the garage-k3s volume, as for auth-pg. The
B2 store `weather-pg-b2` stays declared for its pre-2026-10-31 history; the restore drill
reads `weather-pg-local` with the reader key.
The writer and reader Secrets (`weather-pg-backup-s3-{writer,reader}-k3s`) are created
create-only in the cluster by the change-gate and carry only `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY`; the Garage region `garage` comes from the sidecar env in
`object-store-local.yaml`, not from the Secret. The dated key-name attestation is
`deploy/k8s/database/weather-pg-backup-credential-contract.yaml`; refresh it with its
range-only probe after any credential change.
Read archiving state with
`kubectl -n weather get objectstores.barmancloud.cnpg.io weather-pg-local -o jsonpath='{.status}'`.

### Backup freshness

`WeatherDbBaseBackupStale` fires when the newest available Barman base backup is over 26 h old
(the daily base runs at 09:15 UTC on a standby), or when the metric is absent.
1. `kubectl -n weather get scheduledbackups weather-pg-daily` (LAST BACKUP) and
   `kubectl -n weather get objectstores.barmancloud.cnpg.io weather-pg-local -o jsonpath='{.status}'`
   (`lastSuccessfulBackupTime`, `firstRecoverabilityPoint`).
2. `kubectl -n weather get backups.postgresql.cnpg.io` shows a failed Backup and its message. The
   usual causes are B2 reachability or an expired or revoked writer key; a standby that is not ready
   also skips the run.
3. Re-run once with a one-off `Backup` (`method: plugin`, plugin `barman-cloud.cloudnative-pg.io`)
   after fixing the cause. Never mint a replacement key; recover the existing identity from KSOPS
   custody. If bases fail for more than 7 days, ask storage-infra to suspend the `cnpg/` lifecycle
   rule (no keep-last floor, see above).

### WAL archive freshness

`WeatherDbWalArchiveStale` fires when `pg_stat_archiver` reports no archived WAL for 30 min
(`archive_timeout=300s` forces a segment at least every 5 minutes), or when the metric is absent.
1. `kubectl -n weather get cluster weather-pg -o jsonpath='{.status.conditions}'`: check
   `ContinuousArchiving`.
2. In the primary: `select * from pg_stat_archiver;` (`failed_count`, `last_failed_wal`) and the
   plugin sidecar's logs.
3. Fix the cause (B2 reachability or the writer key). Never delete WAL by hand or disable archiving;
   `pg_wal` grows until archiving resumes, so watch the volume.

## Switchover

There is no `kubectl cnpg` plugin on the operator Mac. Pick a healthy streaming
standby, then:

```sh
kubectl --context vallery -n weather patch clusters.postgresql.cnpg.io weather-pg \
  --type merge --subresource status -p '{"status":{"targetPrimary":"<standby>"}}'
```

Measured on 2026-10-24 (`weather-pg-1` → `weather-pg-2`, writer inserting every
0.5 s): **14.7 s** without a successful write. Every prior row was present on
the new primary, and quorum sync re-formed with the old primary as a replica.

## PITR drill

`scripts/restore-drill.sh --operation <id> --evidence <path outside the repo> --run`
does the following:

1. Inserts three sentinel rows one minute apart in
   `weather_recovery_drill.sentinel`.
2. Forces a WAL switch and waits until it is archived.
3. Restores a `weather-drill-pg-<id>` Cluster from `weather-pg-local` (the Garage
   store production archives to), using only the read-only key
   `weather-pg-backup-s3-reader-k3s` and no WAL archiver, to a target between
   sentinels 1 and 2. The drill NetworkPolicy admits only DNS, garage-k3s :3900 and
   the API servers :6443, so the restore needs no WAN.
4. Verifies that sentinels 0 and 1 are present, sentinel 2 is absent and pgvector
   is 0.8.6.

`--cleanup` removes the drill Cluster, PVCs, ObjectStore and policy. A
failed drill is preserved for inspection; never rerun the same operation ID.

Measured on 2026-10-24 (`pitr20261024a`): the forced WAL was archived before
the restore. **RTO 74 s** (restore start to verified readback). **RPO 1 s**
against the target (60 s sentinel resolution). The drill resources were then
deleted. That drill read the B2 store; since storage-infra#1253 the drill reads the local
store. Its preflight refuses unless the WAL archiver is `weather-pg-local` and that store
reports a `firstRecoverabilityPoint`. Production RPO for an unplanned loss is bounded by
`archive_timeout` (300 s) plus the archive lag.

After a real production recovery, give the recovered cluster a new Barman
`serverName`. Never point a recovered writer at the source archive prefix.

## Schema, migrations and durable work

Weather owns the `weather` schema and the `public_api` views inside its
dedicated `weather` database. Revision `0001_baseline` (#74) holds the
slice-1 schema, `0002_api` (#14) adds the API's views, `0003_publication`
(#18) the publication catalog, `0004_search` (#25) the search tables, `0005_events` (#41)
weather events, `0006_render` (#15) the frozen solar schedule and render segments, `0009_stills`
(#38) hour slots, archived originals and comparison stills, `0007_previews` (#39) delayed
previews, `0010_cleanup` (#19) the staging inventory and the deletion and object-lifecycle
audits, `0011_gc_kept` (#19) its `gc_kept` marker, and `0008_keograms` (#37, chained after
`0011_gc_kept`) keogram status and bins; all are
inline DDL and import no application module. The restore-drill schema `weather_recovery_drill` is not Alembic's;
the baseline and `alembic check` leave it alone.

| Relation | Purpose |
|---|---|
| `station`, `device` | Retained identities; serials stay in `device.private_id` |
| `telemetry_receipt` | Raw bytes plus SHA-256 for every admitted packet; `(received_at, id)` btree for reproject |
| `obs_st`, `rapid_wind`, `evt_precip`, `evt_strike`, `device_status`, `hub_status`, `obs_air`, `obs_sky` | Typed family rows; unique `(device_id, message_type, source_ts, row_idx)` |
| `weather_day` | One row per America/Denver day, keyed by `local_date` (23, 24 or 25 hours) |
| `job`, `outbox` | Durable work and causal events |
| `public_api.observations` | Public union view of all families (security barrier) |
| `weather_event`, `weather_event_revision`, `weather_event_day` | Events (0005): identity, immutable trigger-stamped revisions with the non-empty interval and provenance CHECKs, per-revision day links ([events runbook](events.md)) |
| `public_api.event_revisions`, `public_api.event_revision_markers`, `public_api.event_days` | Security-barrier event views for the feed (0005) |
| `public_api.obs_st`, `public_api.rapid_wind`, `public_api.station` | API views (0002): typed, gated double-precision columns plus `received_at`, `inserted_at`, `valid` and the allowlisted `invalid_fields`; the station alias. No row filter, so no security barrier; that keeps them flattenable onto the `source_ts` btrees |
| `upload_intent`, `media_asset`, `publication`, `publication_asset`, `video`, `withdrawal`, `product_tombstone` | Publication catalog (0003, #18); see [media operations](media.md) |
| `public_api.assets`, `public_api.videos` | Public media views (0003; security barrier, embargo and `ends_at + 630 s` guards) |
| `embedding_profile`, `search_document`, `search_embedding` | Search (0004): profiles, immutable public-text revisions (final withdrawal, generated English `tsvector`) and unit-norm `vector(2560)` rows bound to the document's text hash. See [search operations](search.md) |
| `public_api.search_documents`, `public_api.search_vectors`, `public_api.search_lexical()` | Security-barrier search views (0004): current public documents past their embargo, and their vectors; a SECURITY DEFINER full-text function with the same filter, so the partial GIN index serves `@@` |
| `hour_slot`, `archival_still`, `still_derivative`, `public_api.stills` | Hourly stills (0009, #38): UTC hour slots keyed by `starts_at` with the `weather_day` FK (a trigger keeps each inside its day); one selection revision per (camera, slot) with the same-day composite FK, its segment, its camera's still publication and the original's (id, class, sha256), one active per (camera, slot); sanitized derivatives keyed by (still, role). See [media operations](media.md#hourly-originals-and-comparison-stills) |
| `keogram`, `keogram_bin` | Keograms (0008, #37): per-segment status (`pending`/`published`/`partial`/`unavailable`/`failed`, CHECKed result and `public_not_before >= capture_upper_bound + 630 s`) and the streamed bins (one selected capture and ≤512-pixel column per bin; private, deleted once the PNG commits). See [pipeline operations](pipeline.md#keograms) |
| `public_api.keograms` | Security-barrier keogram view (0008): nothing before `ends_at + 630 s`, no asset or metadata before the publication's embargo; `public_api.videos` is replaced with the same columns so `keogram_state` reports `failed`/`unavailable` |
| `solar_cycle`, `segment` | Frozen civil-twilight schedule keyed on `(site, cycle_date)` and one render segment per camera and kind (0006, #15); current segments of a camera never overlap (`btree_gist` exclusion). See [pipeline operations](pipeline.md) |
| `staging_chunk`, `staging_deletion`, `object_event` | Staging inventory (one row per sealed chunk, kept after deletion), the two-phase staging deletion audit, and the append-only object lifecycle audit (`unavailable`, `expired`, `gc_deleted`, `purged`); `media_asset.state` gains `expired` and `state_changed_at` (0010, #19). See [media operations](media.md#staging-cleanup-and-pressure) |
| `analysis`, `analysis_revision`, `ai_raw_response` | AI descriptions (0012, #23): one head per video (state machine, CAS base), append-only revisions pinned to media SHA-256, model, prompt/schema and evidence hashes, and private raw replies capped at 24 h by CHECK. The API reads only `public_api.video_analyses`. See [intelligence](intelligence.md) |
| `editorial_operator`, `editorial_subject`, `editorial_revision`, `editorial_audit` | The private editor (0013, #44): the one-user allowlist (seeded `jason` via `weather-editor`), one CAS head per edited video or event, immutable full-state revisions (human description bound to web SHA-256 and evidence hash, public/private tags, favorite, note, event revision) and immutable audit rows. See [editor](editor.md) |
| `weather.video_editorial_state`, `public_api.video_editorial` | The one effective-description rule (0013): a human description is active only while its binding matches the current rendition and evidence. The public view (security barrier) exposes the active description, the editorial revision and public tags only |

`obs_st` and `rapid_wind` also carry `inserted_at` (0002; `clock_timestamp()` on insert, `-infinity` for older rows), which export cursors snapshot on. They carry a btree on `source_ts`, so current conditions
(#14) read the newest row by index. The other families use BRIN.

### Slice-2/3 DDL removed from the baseline

The baseline does not create the catalog, media, publication, keogram, still,
preview, event, editorial, search or hour-slot tables (#41 has since added events in `0005_events`). Their earlier DDL is at
commit `fc34b83` in `app/weather/db/schema_v1.py` and `app/weather/db/ddl_v1.py`.
That includes the privacy CHECKs (original/bucket/visibility equivalence,
content addressing, the 630-second preview margin, withdrawal reasons) and the
`security_barrier` views over assets, segments, videos, keograms, stills,
previews and events. Each owning issue (#18, #19, #37, #38, #39, #41, #44, #25,
#23) re-creates its tables in its own expand-only migration, with those CHECKs
and its public view behind a security barrier. #18 has done so for
`media_asset`, `video` and the publication tables (`0003_publication`). `0004_search` (#25) replaced the
v1 `search_document`/`embedding_profile` design. #15 did so for
`solar_cycle` and `segment` (`0006_render`), and #38 for the hour slots and stills (`0009_stills`). #39 did so for `camera_preview` and
`public_api.previews` (`0007_previews`), and #37 for the keogram tables and view (`0008_keograms`).

## Connection and role custody

Application processes read `username`, `password` and `ca.crt` from
`/var/run/weather-db` (override the directory with
`DATABASE_CREDENTIAL_DIRECTORY`). Basic-auth Secrets are `weather-pg-<role>`;
the CA comes from CNPG's `weather-pg-ca`. The engine verifies both the CA and
hostname for `weather-pg-rw:5432`, database `weather`. Never log an engine URL,
Secret, environment dump or raw database exception.

`weather.db.connection.wait_for_database` retries the first connection for up
to 60 seconds with backoff, because kube-router programs a new pod's
NetworkPolicy a few seconds after start. The migrator, the scheduler and the
reproject command call it. The receiver's drain doesn't need it: it retries
every second while the spool holds receipts.

| Role | Capability |
|---|---|
| `weather_migrator` | Database/schema owner, DDL and migrations |
| `weather_ingest` | Insert on station and device, select on their non-private columns (not `private_configuration`); select/insert on receipts and family rows; no update/delete |
| `weather_worker` | Read/write `job` and `outbox`; select/insert `weather_day`; select/insert `media_asset`, `upload_intent`, `video` with UPDATE only on the columns the publisher writes; select/insert the append-only `publication`, `publication_asset`, `withdrawal`, `product_tombstone`; the fence sequence; select/insert search documents and vectors, update only a document's `visibility`, `withdrawn_at` and `index_status`; select only `id, device_id, source_ts, values, quality` of `evt_precip`, `evt_strike`, `obs_st` and a device's `station_id` (event rules); select/insert events, revisions and day links, update an event's `revision` pointer; select/insert `solar_cycle` and `segment`, updating only the render result columns; select/insert `keogram` (update only its status columns) and `keogram_bin` (update its sample, delete); select/insert `hour_slot`, `archival_still` and `still_derivative`, updating only a selection's `active`/`retired_at` and a derivative's `state` (both one-way); select on `public_api.obs_st` and `public_api.rapid_wind` for the sidecar weather block (#15); no receipts, serials or other telemetry |
| `weather_api` | Select the `public_api` views (including the two search views `stills` and `keograms`) and execute the two value-gating functions and `search_lexical`; no base-table access or `weather` schema usage |
| `weather_editor` | The operator listener only (#44): select the allowlist; select/insert editorial heads, revisions and audit (head `revision`/`updated_at` update only); #41's event hooks (select/insert events, revisions, day links, `weather_day`, insert `hour_slot`, update an event's `revision`); select/insert `outbox` and `job`; #25's replace/withdraw on `search_document`; select `video`, `publication_asset`, `analysis`, `analysis_revision`, `video_editorial_state`; select the receipt columns the receipts export serves (not `receiver_incarnation`). No DDL, telemetry or catalog writes, no `public_api` access. `weather_api` has nothing in the `weather` schema; `weather_worker` reads `video_editorial_state` for the AI/search projection |

Neither ingest nor API can delete telemetry. No telemetry retention trigger,
TTL or cleanup job exists. Station replacement adds a new device validity
epoch at the same site; it does not erase prior receipts or observations.
Default privileges revoke `EXECUTE` from `PUBLIC` on every function the
migrator creates, so a later function is private until granted.

## Expand/contract migrations

The head is `0013_editorial` (on `0012_analysis`, after `0008_keograms` on `0011_gc_kept`). `0001_baseline` has run on production, so every change is
expand/contract. Add a new revision with inline DDL; never edit an applied
revision or import current application modules into one. `models.metadata`
describes the current schema for `alembic check` and must change in the same
commit as the revision.

The app image contains `alembic.ini`, `app/migrations` and the schema package.
`python -m weather.db.migrate` waits up to 60 seconds for the first connection
and up to 120 seconds for all five CNPG-managed roles, then execs
`python -m alembic upgrade head`. `env.py` runs every revision in one
transaction with `lock_timeout` 10 s and `statement_timeout` 60 s, so a
migration fails rather than queueing behind live traffic. A long backfill
belongs in its own job, not a revision. Startup failure is generic and does
not disclose credentials.

`weather-migrate` is a tokenless Argo **Sync** hook at wave 1, after the
Cluster at wave -5, with a 300-second deadline, no automatic retries and a
one-day completed-job TTL. Every DB-client workload runs at wave ≥2. Don't use
PreSync for a database the same Application creates.

For each schema change:

1. Add compatible columns/tables/views with a new revision and update
   `models.metadata`.
2. Fleet CI upgrades an empty database to head, re-runs the upgrade and
   requires `alembic check` to be clean.
3. Build and promote the app digest through fleet CI; the migrate hook runs
   that digest.
4. Observe the migration Job complete and `alembic current` at the expected
   head, then Argo Synced/Healthy at the promoted digest.
5. Remove obsolete columns/views only in a later compatible revision, after
   every running image has stopped depending on them.

Production downgrade is explicitly disabled. Restore a compatible image with
an expanded schema, or use #6's PITR procedure when a data recovery is required.
Never drop the namespace, database, Cluster, PVCs, buckets or retained tables
to redeploy. Keep source revisions, image digests and recovery coordinates.

## Calendar and the public view

`weather.db.days.ensure_days(connection, start, end)` inserts the missing days
of an inclusive range (at most 366), with their UTC `hour_slot` rows, local offset and
fold (#38). Bounds come from the pinned Python time
model (`weather.timemodel.weather_day`), so PostgreSQL's timezone data never
decides a day. Existing rows never change, and the worker cannot update or
delete them. The scheduler keeps today and the next seven days present.

`public_api.observations` has this stable contract for ingest/API work:

| Column | Meaning |
|---|---|
| `id` | Opaque observation UUID |
| `station_alias`, `device_alias` | Public aliases; never serials or hardware IDs |
| `message_type` | `obs_st`, `rapid_wind`, `evt_precip`, `evt_strike`, `obs_air`, `obs_sky` |
| `observed_at`, `received_at` | Original source instant and retained receipt arrival |
| `row_idx` | Zero-based row in the source packet |
| `values` | Only allowlisted metric keys; a value is numeric only when its row and field are valid, otherwise null |
| `quality` | `valid` flag, a fixed public reason vocabulary and the allowlisted invalid fields |

`device_status` and `hub_status` are deliberately absent from the public
union. The numeric key allowlist is `PUBLIC_METRIC_KEYS` in the baseline
revision. Extend the view through a new revision when a new public metric is
qualified; unknown keys and arbitrary quality text never pass through. The
view is a security barrier, so an API-role predicate function can't see the
private columns it hides. Direct API-role access to base tables stays denied.

## Job ownership and atomic completion

Enqueue work and an outbox cause using a caller-owned transaction. Idempotency
keys bind the kind/topic, payload and original deadline. Reusing a key for
different work raises a conflict. Retrying can move `not_before`, but never
changes `deadline`; database triggers reject deadline changes, and a lease can
never outlive the deadline. New work after an expired deadline needs a new,
explicit identity.

`claim(kinds=…)` takes only the scheduler's registered kinds, using
`FOR UPDATE SKIP LOCKED`. A job of an unregistered kind stays queued until a
release registers it, or until its deadline expires it. Each claim increments
`attempt`, which is the fencing token. Heartbeat and completion require the
same attempt and an unexpired lease. The scheduler runs one handler at a time,
renews its 60-second lease every 15 seconds and stops stale attempts.

Register source-owned handlers in `weather.scheduler.handlers.register`:

```python
async def aggregate(context):
    # Prepare external immutable inputs before the bounded write transaction.
    async def write(connection):
        # Use this connection for catalog changes and causal outbox inserts.
        ...
    await context.commit(write, result={"updated": True})

def register(registry):
    registry.register("aggregate", aggregate, topics=("metric.changed",))
```

`JobContext.commit` locks the owned row, runs catalog writes and completes the
job in one transaction. If completion finds the lease stale or expired, the
catalog writes roll back too. Do not open an independent transaction inside a
handler for publication writes. External uploads must be immutable;
uncommitted orphan objects are reconciled by their owning pipeline issue.
If a network failure makes the final commit acknowledgment uncertain, read the
original job/catalog identities after reconnecting; both changes share one
transaction.

Outbox dispatch locks pending rows with SKIP LOCKED and creates idempotent jobs
before marking those rows dispatched in the same transaction. A row whose
topic has no route is left pending for 10 minutes, so a newer scheduler can
route it during a rollout, and is then **quarantined**: `dispatched_at` is set,
`job_id` stays null and `error='unregistered_topic'`. Later rows still
dispatch. Deploy the scheduler that routes a new topic before its producer;
the scheduler uses the Recreate strategy, so two versions never overlap. After the
route ships, re-queue the retained rows as `weather_worker`:

```sql
UPDATE weather.outbox SET dispatched_at=NULL, error=NULL WHERE error='unregistered_topic';
```

Never delete an outbox row merely to make a queue or alert green.

The scheduler's periodic tick runs every 60 seconds in one transaction. It
enqueues `days:<local date>`, which fills `weather_day` for today and the next
seven days, and its idempotency key and deadline are fixed per day, so repeated
ticks are no-ops. The tick then prunes up to 1,000 rows each of routed outbox
rows dispatched more than 30 days ago and finished jobs whose deadline passed
more than 30 days ago. Quarantined outbox rows are kept.

The scheduler Deployment (one replica, wave 2) is excluded from the root
kustomization until the deploy PR that pins the app digest carrying this code.
Its only egress is CNPG plus DNS; its metrics listener accepts `observability`
on port 9000. It has no Kubernetes token or RBAC.

## Query planning: JIT is off

`jit: 'off'` is set in `deploy/k8s/database/cluster.yaml`. The public views are security barriers with per-row subplans, so the planner's cost estimates run into the millions. Above `jit_above_cost`, every read compiled JIT code. On 2026-10-27 that was 1.47 s of a 1.55 s `public_api.stills` query; with JIT off the query took 63 ms. CNPG applies the change by reload, with no restart. To check a slow public read, run `EXPLAIN (ANALYZE)` read-only on the primary and look for a `JIT:` block.

## Failure signals

| Metric | Interpretation/action |
|---|---|
| `weather_scheduler_database_ready` | Zero: inspect database health/role/CA prerequisites; do not print credentials |
| `weather_scheduler_tick_failures_total` | Periodic work or retention failed; check DB health and the sanitized log class |
| `weather_jobs_failed_total{reason="handler"}` | Inspect the sanitized exception class and retained job; retry keeps the original deadline |
| `weather_job_leases_lost_total` | Check node/DB latency and handler transaction duration; never force a stale completion |
| `weather_outbox_dispatch_failures_total` | Dispatch transaction failed; check DB availability. Events remain durable |
| `weather_outbox_quarantined_total` | A producer emitted a topic with no route; ship the route, then re-queue the rows |

## Disposable integration path

`Dockerfile.ci` derives from the exact production PostgreSQL 16.15 / pgvector
0.8.6 mirror, adds a named UID/GID 1001 and checksum-pinned Python/uv tooling.
Its pinned Node stage and separate `ci/browser/` lock install Playwright 1.64.1
and all three browser families at image build time. Browser dependencies use
the dated Debian main, updates and security snapshots. Their package notices
remain in `/opt/weather-browser-tools`, and browser distribution notices remain
in `/opt/ms-playwright`. Runtime checks use that readable shared browser path;
they never run apt or install browsers in an unprivileged validation container.
The recipe copies `/opt/ffmpeg` from the qualified digest-pinned application
image, retaining its licenses. Its final UID 1001 build step verifies FFmpeg
9.0.2 and libx264 on Debian 13, PostgreSQL 16.15, and all browser executable
paths. Browser launch and fixture qualification remain in fleet browser tests.
Before apt changes, the recipe retains the production base's tzdata package
version and Denver TZif checksum. Both the final image build and integration
runner verify that exact file remains unchanged. Integration reports base and
current package versions alongside pinned Python tzdata/IANA versions; it never
changes global timezone configuration or `TZPATH`.
It is a fleet validation image, not an application runtime or another database
service. Build it through the registry-owned `weather-ci` repo-build profile,
pin its actual returned digest in the agents validation profile, and run the
normal bootstrap plus `scripts/ci.sh all` in that image.

`ci.sh integration` delegates `scripts/ci_integration.sh`. The script refuses
non-Linux, root or non-fleet execution; it creates an isolated temporary
socket-only PostgreSQL, creates test roles/extension, upgrades an empty
database (holding only a stand-in drill sentinel schema, which must survive)
and the test database to head, re-runs the upgrade, checks both Alembic diffs,
and runs real constraints/privacy/concurrency tests. It checks exact PG/extension versions
and the integration marker before destructive **test-only** truncation. A trap
stops the disposable server and removes only its unique temporary directory.
Never run PostgreSQL, these integration tests or image builds on the Mac.
