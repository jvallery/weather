# Private recovery manifests and verification receipts

Status: record boundary selected; central-verifier admission, bootstrap/copy coverage and actual restore remain open under D15. Consumers WX-08/28/29/30/32/34. Implements requirements §15.3–15.4 and the [checkpoint transaction protocol](../implementation/publication-recovery-contract.md). [recovery.schema.json](recovery.schema.json) describes private records, not public API responses or proof of successful recovery.

## Candidate is not success

`checkpoint_candidate` binds one catalog generation and deterministic closure digest to one database system/incarnation/timeline, named restore point and LSN. Select a usable base preceding that target and bind its inventory hash before publishing this candidate. If no such base is available, keep an incomplete attempt; do not emit a candidate with invented backup identity. Creation time is diagnostic, never the recovery target.

Application/database image digests and schema, extension, embedding-profile, active search-generation and recovery configuration inventories are required. Inventory references are private opaque identities; they must resolve through independently recoverable custody. A reference to a Secret name alone is not credential recovery. No secret values, signed URLs, tokens or plaintext keys belong in these records, logs or public repository fixtures.

The candidate lists bounded closure shards, counts and total bytes. Each shard is immutable UTF-8 JSONL of SourceObject records with LF termination, no blank lines or duplicate JSON keys. Rows are sorted by object ID, strictly unique within and across shards, and preserve class, exact size/hash and the private source-locator identity. The ordered shard descriptor list determines the closure digest using the versioned closure policy. The selected [v1 byte contract](recovery-canonicalization.md) specifies JCS serialization, UTF-8 row ordering, 4096-row partitions, domain-separated root hashing and exact database-target hashing. [Synthetic golden vectors](recovery-canonical-vectors.json) were independently recomputed in Python and JavaScript; this does not prove runtime closure completeness.

The ledger includes all retained required media/metadata classes and historical/PITR references from the catalog closure. Database base/WAL/history and bootstrap inventories are separately verified by their ledgers, rather than being mistaken for catalog media. Transient minute previews remain excluded under the established protocol; clear their pointers on restore. Expired videos do not remove retained hourly originals. Objects are counted once by immutable identity; references from several database rows do not create several copies.

Chunked/sharded verification permits bounded memory and resumed work. Schema list ceilings are parsing protections, not permission to omit objects. If an admitted closure needs more shards, introduce and qualify a versioned hierarchy before exceeding the ceiling. No count-only or sampled-hash shortcut can declare a complete closure.

## Exact-version verification

`object_verification` records source identity and destination identity separately. Destination ID, key ID and provider version ID must resolve independently of the original database, NAS and Garage. Never substitute an unversioned latest pointer, ETag or listing timestamp for the exact bytes/hash. Source and destination size/hash must match; this is a semantic check in addition to schema validation.

A new content verification reads the complete destination bytes through the independent reader and computes SHA-256. Reuse is allowed only by referencing a retained, trusted prior receipt for the identical destination/key/version/size/hash and still-valid protection policy. Reject cycles, missing predecessors, mismatched versions and a prior receipt based only on sampling. A hash-equivalent object under another version needs its own verified receipt. Protection evidence records the actual version's retention/lock policy; the bucket default alone is not proof of that version's protection.

The verifier's identity, image digest, policy revision and observation time are recorded. A caller-created schema-valid success document is not authoritative. Accept receipts only from the admitted central-owner verifier custody path and independently validate their provenance. D15/OWN-CUSTODY must specify that trust/discovery path and lifecycle; do not invent a new signer, credential or uploader here.

## Verified checkpoint and incomplete attempts

`verified_checkpoint` binds the exact offsite candidate manifest version, the catalog generation/digest, a database-target digest, every closure-shard verification ledger, and separate base/WAL and bootstrap inventory ledgers. Its missing and mismatch counts are fixed at zero. Incomplete attempts use a different record type with reasons and a private diagnostic ledger; they can never be selected by a success-only restore selector.

Required semantic verification before writing success:

1. Read/hash the candidate from its exact independent destination version and validate its schema. Match checkpoint, generation, closure policy and every recorded identity; reject an unrelated but well-formed candidate.
2. Verify each candidate shard and one corresponding verification ledger, without duplicate/omitted shards. Join every source object exactly once to a trusted destination receipt. Recompute per-shard and global counts/bytes/digest; equality of counts alone does not establish set equality.
3. Read the base/WAL ledger through its exact destination version. It must identify every base component, required WAL segment and timeline-history file, their offsite versions/hashes and the qualified replay chain to the named target. Verify the database-target record's exact hash and field equality with the candidate. A list of filenames or successful latest-WAL copy is not proof of chain continuity or base usability. The central owner must finalize its versioned ledger format and qualification adapter before enrollment.
4. Read the bootstrap ledger and independently resolve required images, configuration, secrets and decryption dependencies. Verify their custody and recovery usability under the required Mac/NAS/DB-independent failure scenario. A reference-only inventory without recoverable bytes/access cannot pass. Stored vectors/profile metadata restore without inference.
5. Commit the success receipt immutably into independently discoverable owner custody. Its own hash and destination version are recorded in the owner discovery index, not recursively inside itself. A mutable latest pointer is a convenience; loss of that pointer must not destroy discovery of retained checkpoints.

An interrupted verifier may keep partial ledgers, but produces no success receipt until all checks complete. If receipt publication acknowledgement is lost, read back the exact immutable version and compare bytes before declaring success or retrying idempotently. A successful ordinary mirror job cannot fill missing proof fields. Missing predecessor receipts or broken discovery are explicit degraded states.

## Restore and lifecycle

Select the newest fully verified common checkpoint by validated target/catalog ordering within compatible database incarnations, not by latest receipt timestamp. Independent incarnation histories are not globally ordered by LSN. Revalidate complete closure before enabling public reads; keep schedulers, cleanup and retention stopped while restoring into a new archive identity. Hash all restored required assets, reconcile database/catalog state and exercise real playback, private denial, weather queries, editorial state and vector search.

Recovery pins cover candidate shards, verification ledgers, base/WAL/history, bootstrap dependencies, prior reused receipts and success discovery. Garbage collection cannot delete a predecessor still needed to establish a retained checkpoint. Verify lifecycle against the supported historical/PITR window, independently of current publication tombstones. No automatic key revocation or provider retention changes are authorized by this schema.

## Acceptance

Synthetic fixtures test shape and separation of success/incomplete records. WX-30 must additionally reject equal-count/different-object closures, wrong manifest version, duplicate/missing shard, source/destination hash mismatch, missing or cyclic reused receipt, incompatible target/incarnation, missing WAL/history and unavailable bootstrap identity. WX-29 qualifies independent-reader and per-version protection evidence; WX-32 performs the isolated full restore. These tests require admitted cluster resources for real archives; the Mac is not the full-archive hashing or restore environment.

Remaining planning work: the central owner's exact base/WAL/bootstrap ledger integration and receipt discovery/trust contract, third-copy coverage, independent bootstrap and capacity/SLO admission. These unresolved items keep D15 open; the schemas do not claim they are solved.
