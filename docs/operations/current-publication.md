# Current telemetry and export publication (WX-13)

Source preparation only. Migration `0003_current_publication` follows
`0002_telemetry`; the PostgreSQL proof cases have **not run**. The migration,
metadata composition and test cleanup changes below must be adopted after #9
and #12. This source does not enable a workload or implement HTTP/cache/cursor
signing or scheduler outbox handlers.

## Stored state and public reads

`weather.metric_current` stores one pointer and positive revision for each
station/public metric. It stores no copied measurement or quality JSON.
`weather.metric_binding` is the migration-owned mapping from public metric IDs
to protocol fields/families/units, frozen from registry `ab637c7`. Derived
`rain_last_hour` and `rain_today` have no direct observation pointer.

An AFTER INSERT trigger advances a bound pointer only when the new observation
has eligible **source time** and strictly newer `source_ts`. A newer observation
with an invalid/null/absent field still advances that metric to its safe null
and reason; retaining an old measurement would silently extend its hold. A bad
source clock does not advance any pointer. An unrelated family does not advance
unbound metrics. Equal source times preserve the existing pointer, even if
receipt time, UUID or arrival order differs. Backfill ties are deterministically
ordered by source time, family and ID before new ingestion resumes.

`public_api.metric_current` joins those pointers to #12's safe telemetry view.
It returns station/device aliases, public metric/unit, opaque observation ID,
source and original receive times, safe value/field quality/row quality and the
metric's revision. It does not calculate freshness, cadence or expiry; the API
must derive those honestly at request time using commissioned policies.

An AFTER UPDATE OF quality trigger revises only pointers whose family/ID still
match the changed canonical row and whose effective safe field changed. A
temperature dispute at T1 immediately yields null/`payload_conflict`, retaining
uncontested humidity. It cannot alter a newer T2 pointer. Retrying the same
incident, or changing only private metadata, adds no current revision. These
changes, qualification and the existing conflict outbox cause share one ingest
transaction and roll back together. Canonical identity, receipt, source time,
row index and first values cannot be updated; the original receipt remains.

## A stable export boundary without a long transaction

`weather.telemetry_publication` contains only `(publication_seq, family,
observation_id)` for the six public families. It is membership metadata, not
another canonical observation table. Application roles cannot mutate it.
`weather.telemetry_publication_clock` has one transactional counter row; no
PostgreSQL sequence, receive timestamp or UUID serves as a commit watermark.

Every supported telemetry write takes that row lock before receipt/evidence or
canonical row locks, holding it until transaction commit/rollback. Successful
new public canonical inserts increment the counter and add membership in the
same transaction. A rollback publishes neither. A duplicate insert or a quality
update does not increment publication membership. The next writer cannot
publish a later counter while an earlier holder remains uncommitted.

`public_api.telemetry_snapshot_watermark()` performs a normal nonlocking MVCC
read of the last committed counter. It fails when the clock row is missing;
zero means a correctly initialized empty publication catalog. A writer already
in flight remains above the returned watermark when it eventually commits,
even if its source time, receipt time or UUID is older than every existing row.

An API request can return its connection immediately after obtaining the
watermark. Prefer one first-page statement with a MATERIALIZED boundary CTE and
a bounded lateral page, returning the watermark even when the page is empty.
This chooses the watermark and first rows under one short statement snapshot.
Later pages query `public_api.telemetry_publications` using:

```sql
WHERE publication_seq <= :watermark
  AND observed_at >= :start AND observed_at < :end
  AND (observed_at, message_type, id) > (:after_time, :after_family, :after_id)
ORDER BY observed_at, message_type, id
LIMIT :limit
```

Omit the keyset condition on the first page. Bind every value. If the API
flattens each observation into multiple metric points, add the public metric ID
as the final stable sort/cursor key. Bind the watermark, filters, ordering and
limit policy into the signed cursor; do not hold an MVCC transaction or pooled
connection across pages.

Membership predicates must use immutable source identity, source-time range and
source-coded metric/family bindings. Do **not** filter members by non-null value,
current validity, support qualification or freshness. A later dispute must keep
the member in its original sort position and return a safe null/reason. The
views always evaluate current qualification; this is stable membership, not a
frozen copy of superseded eligibility.

## Lock order and migration behavior

BEFORE STATEMENT triggers on receipts, the six v2 evidence tables and all eight
canonical families obtain the publication clock before other row locks. This
includes `INSERT ... ON CONFLICT DO NOTHING`: two replay batches visiting the
same receipt IDs in opposite order cannot first hold different receipt rows
and then deadlock while requesting the clock. A batch with only loss evidence
takes the same lock but publishes no observation.

The v2 qualification function originally locks a canonical row before UPDATE.
0003 uses `CREATE OR REPLACE` with identical qualification/outbox logic and a
clock lock first. It preserves the existing function OID, signature and ingest
grant so pooled prepared statements do not retain a renamed callable. There is
no optional hook or alternate bypass function. The quality UPDATE's statement
trigger reacquires the same transaction-owned clock lock safely.

Manual maintenance that mixes telemetry writes or canonical row locks must use
the same clock-first order. The clock serializes bounded ingest transactions;
keep external I/O outside them and keep #12's drain deadline. API watermark and
view reads never request this row lock, so a slow writer cannot reserve API
pool connections across export pages. Investigate ingest latency/spool backlog
and PostgreSQL lock waits before increasing batch or timeout bounds.

The migration first locks retained telemetry tables against concurrent writes,
creates the four new tables/functions/views/triggers, and backfills already
committed public observations. Only afterward can waiting ingestion proceed
under the new lock order. This initial backfill is one transaction; inspect its
size before applying to a long-running installation. Do not delete telemetry,
reset the publication counter or rewrite memberships to repair an incident.
Use an additive correction and retain the source records. Production downgrade
remains unsupported.

## Grants and later integration wiring

The migrator owns all new tables and trigger functions. Ingest receives no new
table privilege; SECURITY DEFINER triggers maintain pointers/membership. API and
worker receive SELECT on the two safe views and EXECUTE only on the scalar
watermark function. The existing qualification function remains ingest-only.
Private publication/binding/current writes and helper execution are not granted.

After the prerequisite sources land, compose current metadata in this order:

```python
metadata = build_metadata()           # immutable v1 snapshot
extend_telemetry(metadata)             # v2, receipt/conflict support
extend_current_publication(metadata)   # v3, this migration
```

The migration already composes these metadata layers privately for table
creation. Add the v3 extension to `weather.db.models` for Alembic drift checks.
Keep earlier migration snapshots unchanged.

Update #9's `clean_test_database` integration fixture to retain reference data:

```python
if t.name not in {"camera", "metric_binding", "telemetry_publication_clock"}
```

Continue truncating test canonical rows, evidence, current pointers and
publication memberships. The new test module resets its own disposable counter
after truncation. Neither cleanup path is permitted on production databases.

## Pending proof

`app/tests/test_current_publication_integration.py` contains nine **unrun**,
socket/marker/version guarded cases: newer-source ordering including invalid
temperature with valid humidity, immutable first values, matching-only conflict
invalidation and idempotence, atomic rollback, pending/late commit exclusion
with old timestamps/UUIDs and separate page transactions, retained null members,
opposite replay-batch lock ordering, qualification lock ordering, narrow role
privileges, deterministic/idempotent committed-history backfill, and a real
seeded-0002-to-head Alembic upgrade/check in a separately generated database
inside the same disposable CI PostgreSQL instance. That case drops only its
own generated database after rechecking the socket/server test marker.

Offline composed metadata/SQL rendering, lint, type checking and test collection
are source checks only. They do not establish PostgreSQL syntax, locking,
permission, migration or live runtime correctness. Execute the cases through
the approved shared fleet image after adopting prerequisite migrations, metadata
composition and reference-data cleanup; retain exact source/image/workflow
evidence before accepting the database change.
