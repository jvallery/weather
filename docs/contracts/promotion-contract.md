# Compact source promotion and transfer receipts

Status: private record format and crash transitions selected; actual NFS durability, grant/reserve and application transactions remain unqualified. Requirements §§6.9–6.10; D03/D07/D16; producers WX-08/18/36/37. The [receipt schema](promotion.schema.json) describes only a **committed durable transfer**, not a copy intent or an archived Garage original. A shape-valid invented receipt never authorizes cleanup.

## Transfer identity and ownership

Register the source-product obligation and source pins before the first candidate can be cleaned up. Stable transfer/idempotency identity binds work generation, entity revision, selected input manifest hash, camera, source members and pending obligation IDs. A retry with identical identity resumes or returns the committed receipt; different input under the same idempotency key is a conflict. A revised hourly selection or keogram recipe creates new work/input identity rather than mutating a receipt.

Destination grant/bundle/file IDs are opaque and resolve through private admitted configuration. No receipt-supplied filesystem path or URL is trusted. The destination must be the separately budgeted promotion area within the same approved NFS grant. Reference its actual grant/durability evidence, not a PVC capacity label or another consumer's receipt. Destination filenames are internally generated from the allowlisted file inventory; reject traversal, links and duplicate IDs. This schema contains no credentials or public serializer.

For `hourly_original`, exactly one source member and one destination file contain identical bytes/hash/length; validate equality semantically. Selection binds actual camera/day/hour/capture/segment identities through the immutable selection hash and obligation records. A cross-twilight hour can retain multiple candidates before selection; this committed-original format describes the selected candidate, not permission to discard other still-needed candidates. An explicit missing-slot decision requires no fabricated transfer.

For `keogram_inputs`, extracted bytes may differ from source JPEGs. The recipe and source-selection hash bind actual observed capture IDs, region/orientation/masks, UTC bins and deterministic extraction. Verify destination checksums and extraction lineage; never require source/destination hash equality for a transformed slice. Retain compact time/bin/coverage metadata in the destination bundle so source deletion cannot destroy the ability to finish or audit the keogram. No AI/interpolated capture qualifies as observed input.

## Commit sequence

1. Claim the current work generation and source pins in the coordinator database. Check selection revision, admitted destination allocation and safe capacity before copying.
2. Write into an exclusive temporary bundle with exact inventory and source/recipe binding. Read back hashes/lengths, reject corruption, and flush files plus directory metadata under the qualified NFS protocol. Seal the immutable bundle atomically. A successful rename or re-readable hash alone is not a durability receipt.
3. Obtain the qualified durability receipt and recheck the entire sealed destination plus actual admission. In one database transaction, lock work/entity and source obligations, reject stale generation/revision, create the destination pin, transfer all named obligations and insert the immutable transfer record. Its `database_commit_receipt_id` is a stable application transaction/receipt identity, not a self-referential content hash. Commit makes the transfer record and destination ownership visible together.
4. A separate cleanup evaluation may release only the source pins whose complete obligations now have proven destination custody. It rechecks the authoritative committed record, active destination pin, input/revision match and absence of other source consumers. Other video, adjacent-segment, hourly or keogram pins remain independent. Receipt bytes copied to a file are not the authority; the committed coordinator state is.
5. Keep destination ownership until verified Garage archival plus the selected-original FK transaction, or durable completed keogram publication, discharges it. This transfer is not `archived`, does not satisfy offsite custody, and does not authorize physical deletion under a deadline or lease expiry.

If the database commit outcome is uncertain, query by stable idempotency key and compare the full bindings. Never infer success from a timed-out caller, destination filename or vanished source pin. Preserve both copies/pins when uncertain. A later current work generation may adopt the identical verified bundle through a fresh authorized transaction; it may not replay a stale generation's authority.

## Crash and race acceptance

| Interruption | Required recovery |
|---|---|
| Before/during copy or destination flush | Source remains pinned; incomplete temporary bytes are never promoted. |
| After seal, before database transaction | Source remains pinned; verify/adopt exact orphan or retain it through approved orphan handling. No source release. |
| During transaction or lost commit acknowledgment | Read back idempotency identity; either both committed destination pin/obligations/receipt exist or none do. Retry safely. |
| After commit, before source release | Both copies may remain protected; independently reevaluate and release only discharged source pins. |
| Stale worker after selection/recipe revision | Reject receipt commit; quarantine obsolete bundle without overwriting current input or deleting another generation. |
| Destination corruption or lost durability before release | Block release and re-extract from retained source. After legitimate release, report product loss/degradation through the accepted storage incident policy, never claim archived success. |
| Another consumer or cross-segment pin still exists | Do not delete the dense chunk even when this transfer succeeded. |
| Archive upload succeeds but FK commit fails | Keep destination pin, reconcile immutable upload and retry transaction; no `archived` receipt yet. |

The schema bounds source/file/obligation arrays to 4,096 and sizes to JSON-safe integers. Stream/hash bytes under actual job limits. If admitted work exceeds metadata bounds, qualify a versioned sharded format; never truncate the source index or obligations to pass validation. Array uniqueness by source attempt/file ID and exact pin/obligation coverage require relational checks in addition to schema validation.

The [12 shape fixtures](promotion-fixtures.json) cover hourly/derived distinction, required durability/commit/input evidence, ownership, generation and inventory bounds. Runtime tests must additionally cover every transition above, wrong hash with valid shape, wrong grant, cross-camera/day/hour lineage, stale revision, incomplete obligations, duplicate attempt/file IDs and a forged receipt ID that resolves to no authoritative commit. Existing NFS/power-loss/fencing tests remain required; this format does not create a new storage guarantee.
