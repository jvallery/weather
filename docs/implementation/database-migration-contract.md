# Database migration and revision contract

Status: design for WX-05/08/11/12/13/17/23/25/28/30/32/37/40/41/42/43. This is a migration contract, not executable DDL. Requirements §10.3 and §17 remain normative. Exact PostgreSQL/extension image and storage admission are D06 qualification gates.

## Schema boundaries and roles

Use separate private ingest, orchestration, editorial and recovery schemas plus a public projection schema. Public API roles read only sanitized projections and invoke bounded query functions; they cannot select raw packets, hardware identities, original-object metadata, operator notes, actor audit or archive configuration. Workers receive only their required write interfaces. Migration ownership is not inherited by runtime roles.

Catalog writes go through revision-checked functions implementing the [publication/recovery lock order](publication-recovery-contract.md). Restrict direct table DML from runtime roles. Migration and maintenance jobs explicitly participate in the catalog freeze, or disable checkpoint creation while performing an exclusive, documented migration. No publicly supplied actor, role, SQL fragment or object key is an authority claim.

## Enforced relational invariants

| Relation | Keys and required constraints | Retention behavior |
|---|---|---|
| Site configuration epoch | Immutable epoch ID; timezone, privately stored location and effective interval; no ambiguous overlapping active epoch | Preserve every epoch referenced by a calendar/cycle |
| Camera | Stable public ID, private adapter configuration separately protected | Referenced cameras are disabled, not cascade-deleted |
| Weather day | Unique (site, local date); positive UTC interval; frozen timezone/config epoch | Stable across product expiry |
| Hour slot | FK to day; unique (day, UTC start); positive UTC interval inside day; offset-bearing label and fold | Distinct repeated local hours; never unique on wall-hour alone |
| Solar cycle / segment | Unique cycle identity and daytime/overnight kind; frozen boundaries, model and allocation; positive UTC interval | Preserve compact identity after video/raw expiry |
| Capture identity | Non-partitioned registry keyed by capture ID; camera and source-segment FKs; immutable time estimate/bounds/hash/geometry | Keep compact referenced rows independently of raw chunks |
| Still selection revision | Composite camera/day/slot/source relationships; at most one active revision per camera/slot; original asset must have original class | Never cascade from video/segment expiry |
| Media asset | Unique immutable ID and unique storage identity; positive bytes, SHA-256, class, recipe/lineage, private provider reference | Explicit tombstone and reference-aware GC |
| Derivative lineage | Parent original/capture and child asset FKs; approved public class, mask/recipe revision; trusted exposure upper bound | Child public eligibility never authorizes parent reads |
| Video publication | Unique camera/segment/media revision; active pointer CAS; partial/unavailable states permit absent video assets | Immutable historical references remain recovery-aware |
| Keogram | Segment/camera FK, recipe and UTC-bin map hash; asset/extraction lineage | Durable product or compact inputs before raw cleanup |
| Preview | Camera FK, generation, source upper bound, earliest-public time and expiry; active pointer CAS | Reconstructible; clear pointers on recovery |
| Editorial revision | Immutable subject/base/evidence revision, authenticated actor/action/reason; bounded allowed fields | Preserve audit; private notes/favorites stay outside public projections |
| Search document/vector | Canonical-text hash, media/evidence/editorial revision; profile and generation; unique embedding idempotency key | Inactive/withdrawn revisions excluded before new embedding completes |
| Job/outbox/pin | Idempotency key, attempt/fence, original deadline, input identity; transactional outbox | Lease expiry does not discard source pin |
| Checkpoint/closure/custody | Unique checkpoint and marker identities; exact object identities/hashes and provider versions | No GC until referenced recovery windows expire under policy |

Use composite unique keys/FKs to enforce camera consistency across source capture, segment and selection. Enforce the slot's day with a composite (slot ID, day ID) reference, rather than accepting unrelated valid IDs. Validate capture timestamp membership and slot containment in transactional write functions: ordinary foreign keys alone do not enforce interval inclusion. Source estimate assigns exactly one half-open segment. Store uncertainty separately; do not claim boundary precision merely from membership.

Asset class requirements are enforced by a composite asset-ID/class reference or an equivalent database constraint, not a UI string. The active still can reference only a private original asset. A public derivative row cannot be relabeled to point to that same original. Prevent changing immutable asset classes or source hashes after references exist.

Allow missing hourly selections as explicit state/reason with no original asset. A selected/archived state requires its complete original receipt and relationships. Expected slots exist independently of capture success. Video partial/unavailable state does not invent an MP4 to satisfy a non-null column.

## Telemetry layout and ingestion

Partition exact-byte receipts and normalized family rows by UTC receive/source time as appropriate, with private immutable receipt identity separate from parser revision. Keep a non-partitioned receipt registry or partition-key-inclusive references so receipt/observation provenance remains enforceable. Specify this physical choice in D05 before DDL; do not assume a global unique index exists across arbitrary partitions.

One datagram receipt can produce multiple indexed observation rows. Persist raw bytes before successful spool acknowledgement. Projection retry identity includes receipt ID, parser version and row ordinal. Canonical observation deduplication is separate: retain receipt links when multiple packets describe the same observation. Malformed, unknown-family and future fields remain recoverable from exact bytes even if no typed row is produced.

Current values advance per metric only when a candidate's source time/quality is admissible. Replayed older data cannot become fresh by acquiring a new received_at. Zero is a value; missing is null with a reason. Late observations revise affected aggregates and evidence bindings through an outbox without altering immutable receipts.

Native time partitions are an operational layout, not a retention policy. No automatic detailed-history drop is authorized. Precreate bounded future partitions and alert on missing partition admission; preserve arrivals in the independent bounded spool rather than silently discarding them. Backfill parser/projection versions in bounded keyset batches with resumable watermarks.

## Revision and transaction boundaries

- Publication inserts verified immutable references, changes the active pointer, increments catalog generation and emits outbox in one transaction.
- Still selection commits original durability and all source/day/hour FKs together. Destination pins are established before source-pin release; the NFS/object steps remain a recoverable saga.
- Editorial updates compare the expected effective revision and bound evidence. Preserve automated output separately. Revoke dependent old search documents in the same transaction; asynchronously create replacement sidecar/embedding jobs.
- Model/profile reindex builds a shadow generation and verifies dimension/profile homogeneity. Activation atomically switches the active profile/generation. Store full vectors for independent restore.
- Measured-event/almanac updates identify algorithm/window/evidence revision. Correcting measured evidence invalidates dependent synthesis/search claims immediately, without withdrawing independent unchanged visual observations.
- Outbox consumers deduplicate on event ID and expected input revision. Acknowledgement follows committed effect, not task dispatch.

## Migration sequence and compatibility

1. Foundation: schema ledger, roles/write interfaces, site/camera/calendar identities, immutable asset classes and job/outbox/pin primitives.
2. Ingest: exact receipts, normalized rows/metrics, parser lineage and history projections.
3. Products: cycles/segments/captures, publication revisions, hourly selections, keograms and preview eligibility.
4. Derived data: evidence aggregates, events, almanac, analysis, editorial revision bindings and public views.
5. Search: extension/profile checks, full-vector representation, document revision constraints and generation activation.
6. Recovery: checkpoint/closure references, custody receipts and complete retention graph.

These are schema dependency stages, not permission to hold independent implementation unnecessarily. Split additive migrations within each stage; do not make all product code wait for every derived projection. WX-08 owns stable shared keys and write interfaces; later owners extend them compatibly.

Use expand/backfill/validate/switch/contract. New readers tolerate the previous supported schema during rollout; old writers cannot bypass new mandatory constraints. Backfills have bounded batches/timeouts, resume keys and observed WAL/replication budgets. Add and validate constraints using the selected PostgreSQL version's supported mechanism, without pretending unvalidated constraints protect historical rows. Lock-heavy changes require a measured maintenance plan.

Keep schema version and supported application-version interval in release metadata and recovery manifests. Refuse startup for incompatible versions. Migration failure stops promotion; it does not roll back committed observation data. Prefer rolling back application images during the compatibility window. Destructive contraction or data reinterpretation needs a preceding verified checkpoint and an explicit data-preserving recovery procedure; down migrations that delete newly captured data are not a rollback strategy.

## Required verification

WX-08 must test cross-camera/day/hour mismatches, duplicate active selections, wrong asset classes, unavailable products, repeated DST hours, leap dates, source-segment boundary ownership, and attempts to bypass catalog write functions. WX-11/12 test multirow packets, byte-identical retry, parser replay, malformed packets and late metrics. WX-43/25 test stale editors/workers and immediate search withdrawal. WX-30/32 test GC against old PITR references and restore all constraints/vectors without inference.

Migration qualification runs from both empty and previous supported schemas using representative bounded fixtures; interruption/resume, constraint validation and image rollback are required. A successful empty database migration does not prove a retained-history upgrade. Private evidence records exact migration/image/extension versions, row counts, checksums, constraint results and downtime/WAL observations. Do not claim this design document is evidence those tests passed.

