# Database, migrations and durable work

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
