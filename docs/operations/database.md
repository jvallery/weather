# Weather database operations

## Commissioning state

The source under `deploy/k8s/database/` is excluded from the application until
weather#2, weather#9, monitoring-stack#1072 and storage-infra#1112 close and the
five login Secrets exist in agents KSOPS. This runbook describes the intended
operation; source validation is not deployment, backup or restore proof. Live
evidence, measured WAL headroom, switchover downtime and PITR RTO/RPO are added
when weather#6 commissions the database.

## Topology and identities

`weather-pg` has three instances on `longhorn-v1-rwo`, with 20Gi per instance,
required anti-affinity across `topology.vallery.net/proxmox-host`, synchronous
replication min/max 1 and `synchronous_commit=on`. Control-plane nodes are
excluded. Per database instance requests are 1 CPU/2Gi, limits 2 CPU/4Gi; the
Barman sidecar adds requests 100m/128Mi and limits 1 CPU/512Mi. A restore drill
adds one similarly bounded instance and one 20Gi PVC. Placement must be checked
against current allocatable resources, reservations, pressure and namespace
quota again before commissioning or a drill.

The Zot image is pinned by digest and was verified against the running Research
Vault primary: PostgreSQL 16.15 and pgvector 0.8.6. Initdb creates database
`weather`, owner `weather_migrator`, and the `vector` extension. Managed roles
`weather_ingest`, `weather_worker`, `weather_api`, `weather_editor` have login but
no superuser, createdb or createrole; the API role has a 40-connection limit.
Migrations supply their grants. The five basic-auth Secrets are
`weather-pg-<role>` in agents `secrets-ksops/weather`, with `username` and
`password`. No database credential belongs in source, a connection URI printed
to a log, or an issue. Superuser password access is disabled.

Private packet identifiers must also stay out of PostgreSQL diagnostics.
`log_error_verbosity=terse` removes duplicate-key DETAIL, both parameter-length
settings are zero, and SQL statement/duration logging is disabled, including
error statements below PANIC. Generic CSV error/SQLSTATE diagnostics remain.
The application must still parameterize writes and validate input: terse mode
is not a sanitizer for arbitrary values in an error's primary message. Do not
enable verbose statement/parameter logging on production to debug ingest.

The migrator runs as a `Sync` hook at wave 1 after the Cluster at wave -5, with
bounded role-login readiness. It uses the projected `weather-pg-migrator`
credentials plus `weather-pg-ca` and verifies TLS for `weather-pg-rw:5432`.
It must never be a PreSync hook during first commissioning. Database-client
Pods carry `weather.vallery.net/database-role` with one of the five role names.
NetworkPolicies allow those clients on 5432, `cnpg-system` on the instance API,
observability on 9187 and same-cluster replication. Instance HTTPS egress covers
the Kubernetes API and B2; native NetworkPolicy cannot select a B2 FQDN. The
bucket key and generated CNPG Kubernetes RBAC restrict authority.

`deploy/acceptance/restore/role-logins.yaml` is an excluded, bounded acceptance
Job template. Render a unique operation and future UTC expiry after all five
Secrets and the CA exist. It mounts only those basic-auth Secrets plus the CA,
has no ServiceAccount token, and logs only role names and privilege booleans.
Every login verifies TLS and proves actual password authentication without
superuser/createdb/createrole; a migration's ability to `SET ROLE` is not that
proof. Retain terminal Job output and remove the exact disposable Job after
verification. Never print a password or place it in a command argument/URI.

The Cluster and ObjectStore carry `Prune=false,Delete=false`; the namespace and
production PVC protection must remain in place. Never delete a production
Cluster/PVC/namespace to redeploy. CNPG's primary PDB is an inherited disclosed
estate limitation, not a reason to change a shared operator or force a drain.

## Backup freshness

The Barman Cloud plugin writes directly to
`s3://weather-db-vallery/cnpg/weather-pg` using the bucket-only
`weather-pg-backup-b2-writer` Secret. Daily plugin base backups run at 09:15 UTC,
prefer a standby and run immediately at commissioning. Data and WAL use gzip.
`ObjectStore weather-pg-b2` has no `retentionPolicy`: the writer cannot
permanently delete versions. Storage-infra owns provider retention and the
independent restore reader; custody/capability evidence is
`jvallery/storage-infra:inventory/weather-b2-custody.json`.

### Plugin retention compatibility

Do not add `retentionPolicy: 30d`. Issue #6 and simplification O1/P7 require
the provider lifecycle to enforce expiry. The installed plugin is the estate
HA wrapper of upstream v0.14.0, revision
`1e13020fe5f09fd8fc29ea03828f7b61375b3721`; its extension changes RPC availability,
not retention or the sidecar. Its
[maintenance implementation](https://github.com/cloudnative-pg/plugin-barman-cloud/blob/1e13020fe5f09fd8fc29ea03828f7b61375b3721/internal/cnpgi/instance/retention.go#L156-L177)
skips cloud pruning when the policy is empty. A configured policy invokes
`DeleteBackupsByPolicy`; an error emits `RetentionPolicyFailed` and stops that
maintenance cycle. This is separate from the
[base-backup RPC](https://github.com/cloudnative-pg/plugin-barman-cloud/blob/1e13020fe5f09fd8fc29ea03828f7b61375b3721/internal/cnpgi/instance/backup.go),
so a retention warning alone does not prove that a completed backup failed.
The [v0.14.0 retention documentation](https://cloudnative-pg.io/plugin-barman-cloud/docs/0.14.0/retention/)
confirms that configured retention runs `barman-cloud-backup-delete`.

The non-delete capability proof specifically covers **permanent version
erasure**. [B2's permissions](https://www.backblaze.com/docs/cloud-storage-s3-compatible-app-keys)
allow delete-by-name with `writeFiles`; only deleting a specific version requires
`deleteFiles`. [Without a version ID](https://www.backblaze.com/apidocs/s3-delete-object),
B2 creates a hide marker. The v0.14.0
[sidecar requirements](https://github.com/cloudnative-pg/plugin-barman-cloud/blob/1e13020fe5f09fd8fc29ea03828f7b61375b3721/containers/sidecar-requirements.in)
pin Barman 3.19.1; that release's
[S3 delete implementation](https://github.com/EnterpriseDB/barman/blob/9444450d405d34b07740505d85dd4f31b9c48639/barman/cloud_providers/aws_s3.py#L569-L576)
sends keys without version IDs. Therefore configuring pruning cannot be assumed
to fail safely: it could hide archive objects and start their hidden-version
expiry early. Keep the field absent; never broaden the writer permissions to
silence retention errors. Empty-policy maintenance still reads the catalog,
removes stale Kubernetes `Backup` metadata and updates the recovery window;
those operations are distinct from deleting provider objects. The source guard
asserts that neither the plugin ObjectStore nor legacy Cluster backup settings
configure retention. This compatibility check is source evidence, not a live
Weather backup/PITR result.

Monitoring-stack#1072 scrapes `estate-cnpg` with `namespace=weather` and
`cnpg_cluster=weather-pg`. It alerts when
`barman_cloud_cloudnative_pg_io_last_available_backup_timestamp` is missing or
older than 93,600 seconds (26 hours), sustained for five minutes. Inspect
`kubectl --context vallery -n weather get backups,scheduledbackups,objectstores`
and the Barman sidecar's generic failure category. Never dump Secret data or a
credential-bearing command while diagnosing provider access. Recover the exact
KSOPS identity from custody instead of minting a replacement.

The policy `recovery.pitr_window_days: 30` is recorded in
`ConfigMap/weather-database-policy`. Provider lifecycle hides `cnpg/` objects at
33 days and deletes hidden versions seven days later. Daily bases plus that
margin retain a base preceding the intended oldest target; 40 days remains
below the 90-day media GC grace minus seven days. A new archive's actual history
starts at its first successful base; it cannot prove 30 days of history on day
one. Check the oldest usable base/WAL chain before selecting a historical
target. Long base-backup gaps can shorten the available PITR window.

## WAL archive freshness

`archive_timeout=300s` bounds idle-station WAL segment closure. For every forced
`pg_switch_wal()` acceptance probe, retain the exact segment identity and verify
its B2 presence within five minutes; `ContinuousArchiving=True` alone is too
weak. `cnpg_pg_stat_archiver_seconds_since_last_archival > 1800`, sustained for
five minutes, fires `WeatherDbWalArchiveStale`. The recording rule
`weather:slo4_db_offsite_ok:ratio` measures the 30-minute offsite-age objective.
Missing observations count as failure in the monitoring contract.

On an archive outage inspect Barman connectivity, key binding and provider
metadata, and retain the instance/WAL state. Never delete WAL by hand, disable
archiving as a recovery shortcut, or allow a deleting application writer. The
instances/PVC rule extension in monitoring-stack alerts on fewer than three
ready production instances and a production database PVC above 80%.

The WAL headroom calculation is measured at commissioning, then repeated after
Tempest ingest starts. Sample `pg_stat_wal.wal_bytes` across a known interval;
also measure filesystem free bytes on the primary. PostgreSQL's 16MiB segments
and a five-minute switch give a floor of `288 * 16MiB = 4.5GiB` per 24 hours.
Use `max(measured_bytes_per_second, 16MiB / 300) * 86400`, add normal retained
WAL/checkpoint allowance and current database growth, and compare to actual
free capacity. `max_wal_size=4GB` is a checkpoint target, not a limit during a
failed archive; `wal_keep_size=1GB` also consumes space. Do not claim a 24-hour
outage envelope until the measured calculation fits. Expand storage through
source when needed; never recreate or truncate a volume.

## Switchover

Read `kubectl --context vallery cnpg status weather-pg -n weather`, select an
actual healthy streaming standby and keep sentinel writes running. Promote it
with `kubectl --context vallery cnpg promote weather-pg <standby> -n weather`.
Record the last successful write, first resumed write, new primary, one sync
standby, the three distinct host labels and sentinel continuity. A failed write
is outage evidence and must not be dropped from the downtime interval.

## Reader-only PITR drill

`deploy/acceptance/restore/drill.yaml` is excluded from Argo and is rendered by
`scripts/restore-drill.sh`. The resources use the required `weather-drill-*`
prefix, disposable/expiry labels, and namespace `weather`; the reader Secret
is referenced in place. The drill ObjectStore uses only
`weather-pg-backup-b2-reader`. The recovery Cluster has no `spec.plugins` WAL
archiver, no managed-role/password rewrite, and no ScheduledBackup. Its
NetworkPolicy admits the operator only, DNS and HTTPS; application ingress and
connections to production 5432 are not admitted. Production archives continue
normally; read-only identity authority plus the absent drill archiver prevent
drill writes under production `serverName`.

Plan without making a cluster change:

```sh
scripts/restore-drill.sh --operation pitr-20261024-r1 \
  --target 2026-10-24T03:00:00+00:00 \
  --evidence /tmp/weather-pitr-20261024-r1.json
```

After the native dependencies close and safe capacity/current prerequisites
are verified, run the same operation/evidence path with `--run` and omit the
plan-only `--target` option. The script inserts three
retained `weather_recovery_drill.sentinel` rows in production, one per minute,
selects a target between the second and third commits, forces and waits for WAL
archival, and recovers using the independent reader. It requires exactly
sentinels 0/1 present and 2 absent, plus vector 0.8.6. The receipt's measured RPO
is target minus the latest restored sentinel, with a 60-second sentinel
resolution; records intentionally after the target are not claimed as loss.
RTO covers recovery apply through verified readiness/readback. These are test
results, not a claim about every historical production record.

A wait timeout preserves the same Cluster and evidence. Inspect its exact
handle; `--verify` waits/reads the existing operation and never restarts it.
Do not rerun `--run` with the same ID, create a replacement because observation
expired, or mark an incomplete restore as successful. Retain and commit the
metadata-only JSON receipt before exact disposable cleanup using `--cleanup`.
Cleanup requires verified evidence, the matching operation labels and PVC owner
UIDs; it removes the drill Cluster, its PVCs, reader ObjectStore and isolation
policy, and records absence. No production data, Secret, bucket or backup is
deleted. A failed drill is preserved for investigation.

After a production recovery, commission a new Barman `serverName` for that
incarnation. Never send a recovered writer into the source archive prefix.
The post-restore media reconcile/mark-unavailable step belongs to the later
joint recovery issue weather#33.

## Schema, migrations and durable work

Weather owns the `weather` schema and `public_api` views inside its dedicated
`weather` database. CNPG topology, archive operations and PITR commissioning
are delivered by WX-05 (#6). The schema and scheduler are WX-08 (#9).

## Connection and role custody

Application processes read `username`, `password` and `ca.crt` from
`/var/run/weather-db` (override the directory with
`DATABASE_CREDENTIAL_DIRECTORY`). Basic-auth Secrets are `weather-pg-<role>`;
the CA comes from CNPG's `weather-pg-ca`. The engine verifies both the CA and
hostname for `weather-pg-rw:5432`, database `weather`. Never log an engine URL,
Secret, environment dump or raw database exception.

| Role | Capability |
|---|---|
| `weather_migrator` | Database/schema owner, DDL and migrations |
| `weather_ingest` | Insert/select retained station/device, receipts and family rows; no update/delete |
| `weather_worker` | Catalog/jobs/outbox writes and retained telemetry reads; no telemetry deletes or allowlist writes |
| `weather_api` | Select `public_api` views and execute bounded operator functions; no base-table access |
| `weather_editor` | Login created by #6; editorial grants arrive with #44 |

Neither ingest nor API can delete telemetry. No telemetry retention trigger,
TTL or cleanup job exists. Station replacement adds a new device validity
epoch at the same site; it does not erase prior receipts or observations.

`public_api.append_editorial_revision` is a security-definer CAS operation.
Its fixed search path and qualified names prevent search-path substitution.
It checks a database-private operator allowlist, initially empty; #44 seeds
the authorized subject and implements the authenticated operator API. The
function appends the revision and its outbox event in the same transaction.
The revision table rejects update and delete even by the migration owner.

## Expand/contract migrations

The Alembic head is `0001_schema`, after `0000_namespace`. The predecessor
creates the retained station/device identities; v1 adds the remaining
relations, constraints, views, functions and grants. Migration snapshots
`schema_v1.py` and `ddl_v1.py` are immutable after release. Extend current
`models.metadata` and add a new version for later changes. Never import mutable
current metadata into an old migration.

The app image contains `alembic.ini`, `app/migrations` and the schema package.
`python -m weather.db.migrate` first waits at most 180 seconds for all five
CNPG-managed roles, then execs `python -m alembic upgrade head`. Its startup
failure is generic and does not disclose credentials. `weather-migrate` is a
tokenless Argo **Sync** hook, wave 1 after database wave -5, with a 300-second
deadline, no automatic retries, and a one-day completed-job TTL. It is excluded
from the root kustomization until #6 has verified database health and Secrets.
Do not use PreSync for a database the same Application creates.

For each schema change:

1. Add compatible columns/tables/views, update the metadata and add a migration.
2. Run the approved cluster integration check from an empty database and the
   previous head, preserving seeded rows; require `alembic check` to be clean.
3. Build/promote the app digest through fleet CI before adding its migration
   hook to the desired state.
4. Observe migration Job completion and the expected Alembic head, then Argo
   Synced/Healthy and the promoted running digest.
5. Remove obsolete columns/views only in a later compatible migration after
   every running image has stopped depending on them.

Production downgrade is explicitly disabled. Restore a compatible image with
an expanded schema, or use #6's PITR procedure when a data recovery is required.
Never drop the namespace, database, Cluster, PVCs, buckets or retained tables
to redeploy. Keep source revisions, image digests and recovery coordinates.

## Calendar, retained originals and public views

`weather.ensure_days(from_date,to_date,site)` and Python `db.days.ensure_days`
take an inclusive range of local dates. They derive America/Denver midnight
UTC bounds, then enumerate exact UTC hours. Slot identity is `(site,starts_at)`;
the repeated fall-back hour has distinct IDs, offsets and fold values. Local
labels alone are never a key. Re-running a range does not replace IDs.
PostgreSQL timezone rules come from the pinned production image; integration
compares the documented DST vectors and pinned Python time-model rules.

Each still has enforceable camera, day/hour, capture, segment and private
original relationships. A composite `(weather_day_id,hour_slot_id)` foreign
key prevents assigning another day's identically named hour. A partial unique
index permits one active still per camera/hour; a correction retires the prior
selection and inserts a new revision in one transaction. All product foreign
keys restrict deletion. Expire/tombstone videos instead of deleting a segment
or video stills reference.

An original must simultaneously have class `original`, bucket
`weather-originals` and private visibility; equivalence checks reject every
partial combination. Public media use immutable `<class>/<sha256>.<extension>`
keys, available state, withdrawal checks and request-time embargo eligibility.
Their `published_at` marker stays null while uploading/preparing, and is set
only in the catalog publication transaction. Available-but-unpublished objects
remain invisible to the API; private originals cannot receive that marker.
Preview rows require trustworthy bounds and at least 630 seconds between the
capture upper bound and eligibility (600-second embargo plus 30-second margin).
No public view returns an original, receipt, private device identity, staging
path, operator allowlist, private note or favorite.
All public views use PostgreSQL security barriers so an API-role predicate
function cannot run on filtered private rows before the view's eligibility
checks. Direct API-role access to base tables remains denied.

`public_api.observations` has this stable contract for ingest/API work:

| Column | Meaning |
|---|---|
| `id` | Opaque observation UUID |
| `station_alias`, `device_alias` | Public aliases; never serials or hardware IDs |
| `message_type` | `obs_st`, `rapid_wind`, `evt_precip`, `evt_strike`, `obs_air`, `obs_sky` |
| `observed_at`, `received_at` | Original source instant and retained receipt arrival |
| `row_idx` | Zero-based row in the source packet |
| `values` | Only allowlisted numeric/null normalized metric keys |
| `quality` | `valid` flag plus a fixed public reason vocabulary |

The eight private family tables retain normalized `values` JSONB and `quality`
with a receipt FK and uniqueness on `(device_id,message_type,source_ts,row_idx)`.
`device_status` and `hub_status` are deliberately absent from the public union.
The public numeric key allowlist is `ddl_v1.PUBLIC_METRIC_KEYS`, including the
Tempest temperature/humidity/wind/pressure/solar/UV/precipitation/lightning
fields. Extend the view through a migration when a new public metric is
qualified; unknown keys and arbitrary quality text never pass through.

## Job ownership and atomic completion

Enqueue work and an outbox cause using a caller-owned transaction. Idempotency
keys bind the kind/topic, payload, expected revision and original deadline.
Reusing a key for different work raises a conflict. Retrying can move
`not_before`, but never changes `deadline`; database triggers reject deadline
changes. New work after an expired deadline needs a new, explicit identity.

Claims use `FOR UPDATE SKIP LOCKED` and atomically increment attempt and fencing
token. Heartbeat and completion require the same owner/token, unexpired lease
and original deadline. The scheduler runs one handler at a time, renews its
60-second lease every 15 seconds and stops stale attempts. A lost heartbeat
cancels the attempt; an unknown kind is failed explicitly.

Register source-owned handlers in `weather.scheduler.handlers.register`:

```python
async def aggregate(context):
    # Prepare external immutable inputs before the bounded write transaction.
    async def write(connection):
        # Use this connection for catalog changes and causal outbox inserts.
        ...
    await context.commit(write, result={"updated": True})
    return None

def register(registry):
    registry.register("aggregate", aggregate, topics=("metric.changed",))
```

`JobContext.commit` locks/rechecks the owner, runs catalog writes, completes the
job and rechecks fencing in one transaction. If completion is stale, expired or
fails, the catalog writes roll back too. Do not open an independent transaction
inside a handler for publication writes. External uploads must be immutable;
uncommitted orphan objects are reconciled by their owning pipeline issue.
If a network failure makes the final commit acknowledgment uncertain, read the
original job/catalog identities after reconnecting; both changes share one
transaction. Do not create another identity or assume an unknown outcome lost
its data.

Outbox dispatch locks rows with SKIP LOCKED and creates idempotent jobs before
marking those rows dispatched in the same transaction. Unknown topics remain
durable and fail loudly, with a 30-second dispatch backoff; already queued jobs
continue to run. Add the missing source-owned handler/routing, test and promote
it, then the retained event can dispatch. Never delete an outbox row merely to
make a queue or alert green.

The scheduler Deployment ships at zero replicas and is excluded from the root
kustomization. #14 registers aggregate work and commissions one worker only
after its role Secret and DB are ready. Its only egress is CNPG plus DNS; its
metrics listener accepts `observability` on port 9000. It has no Kubernetes
token or RBAC.

## Failure signals

| Metric | Interpretation/action |
|---|---|
| `weather_scheduler_database_ready` | Zero: inspect database health/role/CA prerequisites; do not print credentials |
| `weather_jobs_failed_total{reason="unregistered"}` | Deploy the missing handler or correct the producer; keep failed work for audit |
| `weather_jobs_failed_total{reason="handler"}` | Inspect the sanitized exception class and retained job; retry keeps the original deadline |
| `weather_job_leases_lost_total` | Check node/DB latency and handler transaction duration; never force a stale completion |
| `weather_outbox_dispatch_failures_total` | Check source routing and DB availability; events remain durable |

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
socket-only PostgreSQL, creates test roles/extension, upgrades one empty
database directly to head and another through the predecessor with a retained
seed, checks both Alembic diffs, and runs real
constraints/privacy/concurrency tests. It checks exact PG/extension versions
and the integration marker before destructive **test-only** truncation. A trap
stops the disposable server and removes only its unique temporary directory.
Never run PostgreSQL, these integration tests or image builds on the Mac.
