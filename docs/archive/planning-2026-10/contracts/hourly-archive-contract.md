# Final hourly-original archive and foreign-key receipt

Status: private receipt format and transaction sequence selected; actual Garage immutability/durability, database enforcement and retention admission remain unqualified. Requirements §§6.9, 11.3, 15.3–15.5 and 17.4; producers WX-08/17/18/30/37, integration WX-33 and recovery WX-32. The [schema](hourly-archive.schema.json) describes a committed native original, not a staging copy, public derivative or offsite checkpoint.

## Identity and byte custody

Bind the stable work generation, expected immutable selection revision/hash, camera, calendar date, day ID, elapsed-hour slot ID/UTC bounds/offset label, capture attempt and source interval to one receipt. Repeated local hours have different UTC slots and stable identities; the display label is not a primary key. Selection policy and signed midpoint offset describe the actual chosen source, including qualified fallback. Resolve those values from the authoritative selection, never from caller-supplied filenames.

Source custody may be a sealed capture chunk or a committed compact promotion. Both require an active pin and verified native JPEG identity. The destination original's bytes and SHA-256 must exactly equal the source's. Decode safely to verify native dimensions/MIME without altering bytes. A resized, stripped or recompressed derivative cannot satisfy original archival, even if visually identical. Use separately qualified public derivatives; this receipt has no public URL.

Opaque object/receipt IDs resolve through admitted private configuration and authoritative custody. They are not arbitrary filesystem paths, provider URLs or credentials. The original class and visibility are fixed to private hourly original. Native Garage versioning is not assumed; the qualified immutable-create mechanism and local-durability receipt bind the actual destination identity and bytes. ETag, PUT success or hash readback alone is not a local-durability guarantee. Offsite provider-version verification remains a later, separate checkpoint obligation.

## Commit and retry sequence

1. Resolve the current selection/work generation and active source pin. Check the actual camera/day/slot/capture relationships, source eligibility and qualified fallback policy. Missing selection remains an explicit missing slot; do not fabricate an archived receipt.
2. Upload native bytes through the archive-authorized boundary to an immutable object identity. Reuse on retry only after exact identity/size/hash verification under the qualified write protocol; a conflicting existing object fails closed. Obtain complete-byte verification and actual local-durability evidence. Keep the source pinned throughout.
3. Start the catalog-changing transaction using the shared checkpoint lock **before** row locks, as required by the publication/recovery contract. Lock selection/work/slot and reject stale generation or changed selection. Enforce camera/day/hour/source foreign keys in the database, not just JSON validation. Preserve the actual local-day/UTC slot mapping and private asset classification.
4. In that transaction insert the original asset/revision and enforced relationships, bind retention/recovery policy versions and their chosen retention anchor, advance catalog/publication generation, and insert the immutable archive receipt with its database commit identity. Record completion of the hourly-original obligation and enqueue any derivative/recovery work atomically. Original archival does not wait for a playable video or public derivative. Do not mark public image availability from this private commit.
5. Only after the commit can a separate cleanup evaluation discharge the corresponding source/promotion obligation. It rechecks the authoritative receipt, current relevant ownership, qualified destination durability and every other pin/consumer. The receipt is neither a deletion command nor proof of offsite recovery. Recovery/PITR and class-retention references continue protecting the archived original.

Idempotence binds the entire selection/input/destination/transaction identity, not merely the same slot. A lost commit acknowledgement is resolved by reading the stable idempotency key and comparing full bindings. If absent or uncertain, preserve source custody. A stale worker cannot replay an old receipt as current authority; a newer generation must explicitly adopt still-valid work through a fresh authorized transaction. A replacement selection creates a new original revision and retains the prior immutable record as required by historical recovery references.

## Required semantic and failure checks

The shape fixtures deliberately do not prove these relational conditions:

- Source/destination size/hash equality, genuine decoded JPEG geometry and exact current source-custody receipt/pin binding.
- Day belongs to the camera/site calendar policy; slot belongs to that day; capture belongs to that camera and the qualified same-hour selection. UTC intervals are ordered and local labels match pinned timezone rules, including both DST folds. Exposure uncertainty must satisfy the selection policy; do not require a fictitious exact exposure instant.
- Work generation, selection revision/hash, policy identity/authority and catalog rows match the committing transaction. The policy-defined retention anchor and lifecycle must preserve the selected five-year original horizon independently of video expiry.
- Failure before upload, after upload, during FK insertion, after commit but before acknowledgement, and during cleanup never leaves an unprotected selected original or marks an orphan as archived. Wrong-camera/day/slot, public-class substitution and byte mismatch must fail before obligation discharge.
- Existing orphan objects may be adopted only through exact verification and current authority; orphan GC respects pins, grace and recovery references. Public origin denies direct, alias, range, HEAD and restore paths to these private bytes.
- B2-only restore checks exact original bytes and relationships against the joint closure, including a video expired before its still and a missing/mismatched original. Restored schedulers/cleanup stay stopped until validation.

The [12 fixtures](hourly-archive-fixtures.json) cover structural private-class/FK/receipt/policy/byte/date boundaries and both source-custody kinds. Real object-write semantics, power-loss survival, transactions, races, FK enforcement and independent recovery remain implementation and owner qualification work. No upload, database mutation, lifecycle change or cleanup was performed to define this contract.
