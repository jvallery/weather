# Publication, retention and joint recovery protocol

Status: selected design contract for implementation; not runtime qualification. Requirements R07/R17/R18/R19/R23/R30–R36; decisions D03/D07/D08/D15/D16. Consumers: WX-08/17/18/28/29/30/32/37/38/43. No infrastructure or application code is deployed by this document.

## Persistent identities and authority

An asset is immutable and identified by opaque asset ID, class, bytes and SHA-256. Provider ETags are transport metadata, not content hashes. Reusing an asset ID with different bytes is a conflict. API serializers resolve asset IDs; a client-supplied bucket/key is never a public read capability.

Every catalog mutation carries an expected entity revision and, for worker commits, the current job fencing generation. The transaction locks the job/entity rows, rechecks lease expiry using database time, checks expected input identities, then commits references and outbox together. A lease expiry alone does not cancel a remote operation or authorize input deletion. Stale attempts may leave unreferenced uploads but cannot advance active pointers.

Catalog generation is a transactionally incremented value covering publication, tombstones, durable image selection, editorial/evidence revisions, search-generation activation and recovery-reference changes. Generation allocation and row mutation commit together. Gaps in numerical allocation are harmless; generation numbers are not wall-clock timestamps.

## Immutable publication

1. Claim work and pins against sealed input hashes. A running attempt cannot change its source manifest.
2. Produce and validate the complete mandatory asset set, including real-frame posters and initial sanitized metadata with explicit pending analysis.
3. Upload through the class-authorized gateway under immutable identities. Verify exact bytes/hash plus the admitted local durability receipt. A visibility-only receipt cannot authorize raw cleanup.
4. In a single transaction, enforce expected input/revision/lease, insert references, advance active publication and catalog generation, and emit the outbox record. A duplicate idempotency key returns the prior committed result only when its full input identity matches.
5. The public projection resolves the committed revision. Internal same-origin readback validates metadata and mandatory asset access, including size/hash or the qualified validation method. Record the readback against this publication generation.
6. Cleanup independently evaluates every source obligation; publishing a video does not erase still or keogram obligations.

An object-store outage before commit leaves no visible publication. A database failure after upload leaves an orphan. Reconciliation either attaches the exact expected orphan during a valid retry or retains it through the orphan grace and recovery-reference checks before deletion. Failed readback marks that publication unavailable/degraded and blocks cleanup; it never fabricates a second successful publication.

Metadata updates use new immutable sidecars. A failed sidecar upload cannot advance the active metadata pointer. The canonical public API and sidecar bind the same revision. AI, editor and search tasks must reject an obsolete revision even when their bytes are otherwise valid.

## Source pins and compact transfer

The [private promotion receipt and crash protocol](../contracts/promotion-contract.md) binds the transfer record, inventory, obligations and authoritative commit; schema shape alone cannot release a pin.

Pins have explicit owners, immutable input identity, purpose and completion state. A job's deadline is not a pin's deletion deadline. Cleanup may remove a sealed chunk only after all required video validations, local durability, publication commit and origin readback succeed and every pin/obligation has been discharged or safely transferred.

A compact transfer copies exact selected originals or qualified keogram extraction inputs into the bounded promotion area of the same approved NFS grant. Flush and verify the destination under the admitted filesystem protocol. Commit a transfer receipt binding source and destination hashes and acquire destination ownership before releasing the source pin. During a crash both copies may remain pinned; neither may become unprotected. Reconcile by receipt and hash, never file age alone.

Cross-twilight hour selection registers an obligation before either segment can clean up. Selection spans only the actual hour; it does not grant the image to both segment manifests. A missing slot must be a completed, reasoned selection decision. Pending promotion is not an archived original. The compact copy remains pinned until the original's Garage durability and camera/day/hour/source foreign-key transaction commit.

A permanent extraction failure triggers admitted backpressure and explicit loss handling. Automatic deadline expiry cannot discard the last evidence. The precise capacity, maximum incident age and permitted terminal-loss action remain D03/D16 admission parameters.

## Origin eligibility and withdrawal

Every public image has a trusted latest plausible exposure time. Earliest eligibility is that time plus 600 seconds. Unknown or unbounded exposure time is ineligible. Evaluate eligibility, class, current visibility and expiry at origin before GET, HEAD, range, download, alias and thumbnail responses. Listing omission alone is not enforcement. Derivatives of newly archived originals obey the same rule.

Publish immutable URLs only after eligibility. Before eligibility respond without private existence/timing details and prevent negative/positive cache reuse that bypasses the decision. Clock health failure closes eligibility rather than choosing the client clock. A preview refresh does not alter capture time or its expiry. Stale pointers cannot resolve an expired/withdrawn asset.

Withdrawal commits a tombstone and immediately excludes the affected revision from API/search projections. Origin authorization checks the tombstone. Browser/CDN caches cannot be recalled by database changes: the public cache contract must bound revocation delay and require purge where enabled. D14/D16 must qualify this bound before caching private-risk images. Retained recovery bytes are not public authorization.

## Joint checkpoint: selected algorithm

Use a dedicated primary database session, not transaction-pool session hopping. Reserve one catalog-freeze advisory-lock key. All catalog-changing transactions acquire its shared transaction lock *before* any catalog row lock. The checkpoint coordinator acquires its exclusive session lock. Telemetry receipt/observation ingestion does not take this lock; writes that change published derived evidence do.

Advisory locks require cooperative enforcement. Application roles cannot write protected catalog tables directly: approved write functions/triggers enforce the lock and revision checks, with narrowly scoped ownership and fixed search paths. Migrations and retention follow the same ordering. Qualification must prove no bypassing role/path exists. This is a Weather design constraint, not a property supplied automatically by PostgreSQL.

1. Allocate a unique checkpoint ID and restore-point name. Acquire the exclusive catalog lock with a finite timeout on the current primary. Failure to acquire produces a retryable incomplete checkpoint.
2. In a fresh transaction after acquiring the lock, materialize the recovery object closure at the stable catalog generation, including all retained originals/derivatives, keograms/maps, video/posters/sidecars, required historical revisions and retention references. Persist closure rows, their deterministic digest, schema/image/profile identities and object pins. Commit this transaction.
3. While still holding the same session lock, create the unique named WAL restore point. Record returned LSN and primary system/timeline identity in the external candidate receipt. The checkpoint row/closure commit precedes the marker, and catalog changes cannot occur between them.
4. Release the exclusive lock and terminate the dedicated connection in a finally path. Upload a private immutable candidate manifest outside the frozen window. The manifest contains the checkpoint ID, catalog generation/digest, marker/LSN, cluster incarnation, schema/profile/config identities and full object closure or hashed immutable closure shards.
5. Central custody verifies a usable base backup preceding the marker, contiguous WAL and required timeline history through it, every required object's exact offsite version/bytes/hash, and the manifest/shards themselves. Sampling is not closure verification. Write a separate immutable offsite verification receipt binding the manifest hash and exact provider versions.
6. Only that final verified set is eligible as a joint recovery checkpoint. The production DB may record its verified status later, but recovery must find the manifest and verification receipt independently of that DB.

Do not embed the final manifest's own hash inside its hashed bytes. Store hashes in the next-level receipt. The final receipt is independently discoverable by the owner recovery process; a successful ordinary mirror run is insufficient.

The freeze covers catalog consistency, not all telemetry transactions. Telemetry committed through the restore marker is recovered naturally. A later DB-only target may contain additional measurements but is not the same joint application checkpoint.

PostgreSQL supplies a named WAL marker and session/transaction advisory locks; the ordering and object-closure protocol above are our proposed composition and require isolated tests. See [restore-point functions](https://www.postgresql.org/docs/16/functions-admin.html), [advisory locking](https://www.postgresql.org/docs/16/explicit-locking.html#ADVISORY-LOCKS), and [named recovery targets](https://www.postgresql.org/docs/16/runtime-config-wal.html#RUNTIME-CONFIG-WAL-RECOVERY-TARGET), reviewed 2026-10-23. D06 must revalidate these against the selected installed PostgreSQL major.

## Crash, failover and retention rules

| Interruption | Required reconciliation |
|---|---|
| Before closure commit | No recoverable candidate; ordinary transaction rollback. |
| After closure commit, before marker | Incomplete candidate and retained pins. Never invent a target from wall-clock time. Retry with a new checkpoint identity. |
| Marker created, response lost | Candidate remains incomplete unless independent WAL evidence uniquely proves the intended marker and binding. Simpler default: make a new checkpoint; never reuse a marker name. |
| Session/primary lost at any stage | Abort qualification of that attempt, release by connection loss, re-read incarnation/timeline and restart with a new ID. No inherited advisory-lock assumption. |
| Manifest uploaded, some objects/WAL absent offsite | Incomplete; retain pins and retry bounded custody. No joint-success metric. |
| Verified receipt absent from restored DB | Expected: verification happened after the target. Trust independently verified receipt, then revalidate its closure against restored rows. |
| Expiry races with checkpoint creation | Retention takes the catalog lock and evaluates active/PITR/checkpoint pins before enqueueing deletion. Recheck before delete; tombstoning alone is insufficient. |

Keep checkpoint and PITR references for the supported recovery window; do not remove source objects until the exact recoverable offsite version is independently verified and the applicable local/offsite retention policies permit it. A supported arbitrary PITR target must have its required closure retained or explicitly be classified DB-only. A daily joint checkpoint does not prove full media recovery at every intervening WAL target.

Rolling minute previews are excluded from the durable historical closure. Full custody still accounts for their churn. During restore clear preview pointers before enabling public reads and require newly eligible captures; retained stale bytes cannot resurrect a current view. Hourly originals and keograms are never excluded under this exception.

## Isolated acceptance and evidence

WX-32 must recover with original CNPG, Garage, NAS and inference unreachable. Restore the named target into a new archive incarnation, keep schedulers/retention disabled, compare restored checkpoint generation and closure digest, resolve every offsite version, hash all restored required assets, check FKs/editorial/search profiles and play media through the isolated origin. Rebuild ANN from stored vectors if needed, without re-embedding.

Inject each interruption above and races with publication, correction, hourly transfer and GC. Include one missing WAL segment, one missing original, one wrong private/public class, a tombstoned PITR asset, duplicate marker attempt, lost primary and a missing verification receipt. Each invalid set must be rejected, not silently downgraded to verified joint recovery.

Record measured freeze duration, blocked publication latency, copy bytes/counts, hash coverage, achieved database/media/joint ages and full recovery time. Numeric budgets remain admission/measurement gates; this protocol makes no throughput or RPO guarantee. Store raw receipts privately and publish only sanitized acceptance summaries.

