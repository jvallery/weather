# Public telemetry evidence revisions

Revision `0004_evidence_revisions` retains the public qualification projection
needed to recompute an aggregate at a stable snapshot watermark. Original
canonical telemetry and migrations v1–v3 remain unchanged. API raw observation
pagination can retain stable membership while displaying current eligibility;
aggregate calculation instead reads the immutable revision history.

`weather.telemetry_evidence_revision` contains only `revision_seq` and the safe
`public_api.telemetry_publications` projection: `publication_seq`, opaque `id`,
station/device aliases, public family, observed/received times, row index,
allowlisted numeric values, field quality and public metric quality. It contains
no receipt identity, raw payload, private device identity, coordinates, private
configuration or status-family rows. UPDATE and DELETE are rejected by a trigger,
including attempts by the table owner. Application roles have no raw-table
privileges; only API and worker receive SELECT on the security-barrier view
`public_api.telemetry_evidence_revisions`. Internal trigger functions have no
PUBLIC/application-role execute grants. Administrative DDL remains an owner
capability; this is not an object-lock mechanism against a database owner.

The existing `telemetry_publication_clock` serializes all publications and
public qualification changes transactionally. An initial row's AFTER INSERT
trigger is named `telemetry_revision_published`, which PostgreSQL executes after
v3's `telemetry_published`; it reuses that observation's `publication_seq` as its
first `revision_seq`. AFTER UPDATE compares only the existing sanitized numeric
values, field-quality and public-quality functions. An effective public change
allocates a new clock value and retains the safe projection as another row.
Private-only quality changes do not advance the clock or enqueue an event. The
existing v3 BEFORE STATEMENT clock lock precedes canonical-row locks; this
revision does not introduce an independent sequence or a second lock order.

At watermark `W`, use the latest retained row per observation/family:

```sql
SELECT DISTINCT ON (id,message_type) *
FROM public_api.telemetry_evidence_revisions
WHERE revision_seq <= :watermark
ORDER BY id,message_type,revision_seq DESC;
```

Apply the API's existing fixed station/window/row budgets to that safe view.
Never join the result back to current canonical values or qualification: doing
so would change an older snapshot after a later conflict. A reader may release
its database transaction between pages/recomputations; retained revisions and
commit-serialized clock positions preserve the same public evidence at `W`.

Each new observation or effective public qualification revision atomically
inserts `telemetry.changed` into the existing outbox. Its idempotency key is
`telemetry-evidence:<revision_seq>` and its payload has exactly `id`, `family`,
`station`, `sourceTime` and `watermark`; it includes neither quality details nor
private source identifiers. `not_before` is the event timestamp and `deadline`
is exactly one day later. Any outbox insertion failure rolls back the evidence,
clock allocation and originating canonical change. The worker must register
this topic in the same release before migration/ingest activation; no unknown
live topic is acceptable. The existing `telemetry.conflict` behavior remains.

The migration takes v3's receipt/evidence/canonical table-lock order, locks the
publication relations and the same clock row, then installs the new table,
triggers and backfill in one migration transaction. Backfill snapshots current
safe publications at their original publication positions without incrementing
the clock or emitting outbox messages. Qualification history predating this
migration was not recorded and cannot be reconstructed; backfill is a cutover
baseline, not proof of earlier quality states. Production downgrade is disabled.
Do not remove original rows or old revisions to redeploy.

Source checks cover allowed columns, authority, trigger order and backfill
behavior. Eight PostgreSQL integration cases are prepared for the approved
fleet-only PostgreSQL 16.15 / pgvector 0.8.6 disposable socket fixture. They cover
same-watermark stability, initial publication/event shape, private-only no-ops,
append-only/privacy grants, commit/rollback serialization, backfill without
outbox flood and atomic rollback on event failure. They are **UNRUN** at this
source checkpoint. No database was started or contacted on the operator Mac.
Root integration must extend current SQLAlchemy metadata with `schema_v4` and
preserve predecessor test cleanup while accounting for the new outbox topic.
