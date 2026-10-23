# D15 — central custody and independent application recovery

Status: **protocol and integration target selected; material research still open**. This is not a completed ADR or production acceptance. Requirements R14/R16/R17/R19/R22/R23/R26/R29–R36; implementation WX-28/29/30/32/34; external prerequisite [OWN-CUSTODY](https://github.com/jvallery/storage-infra/issues/1109).

## Decision and purpose

Use the supported Barman Cloud Plugin to archive Weather's physical base backups and continuous WAL into the private database hierarchy of the one Weather Garage bucket. Enroll that bucket in central full and exact-WAL custody. Preserve separate archive identity per database incarnation and independently recoverable authorization. This follows the accepted local-then-offsite policy while keeping media publication and telemetry independent of inference.

Use the existing central B2 destination with its live-verified private, provider-encrypted, 45-day governance default and scoped writer/reader. [E02](../research/custody-evidence.md) proves current policy and capability metadata, not Weather enrollment or complete object protection.

Bind application catalog state to a named WAL marker using the [publication/recovery protocol](../implementation/publication-recovery-contract.md). The checkpoint's full immutable object closure, base/WAL chain and manifest must be independently verified offsite before marking joint recovery successful. Maintain database-only, media and joint ages separately.

The [private recovery contract and schema](../contracts/recovery-contract.md) separate candidates, per-object exact-version verification, incomplete attempts and verified checkpoints. Success requires complete set equality and qualified base/WAL/bootstrap ledgers, not merely matching counts or schema-valid fields. The central-owner ledger formats, receipt provenance/discovery and admission remain unresolved integration obligations.

## Why these choices

- Existing central custody supplies a supported owner, schedule, paced NAS access, independent observer and scoped offsite identities. Reusing this integration avoids an unowned second uploader.
- Barman physical recovery preserves canonical receipts/observations, audit, vectors and profile identity without re-running AI. Logical exports alone cannot replace continuous recovery.
- The catalog freeze and named marker establish a database/object relationship; independent wall-clock snapshots do not.
- Exact-version hash receipts supplement the central verifier's size census and bounded sample. Prior receipts can be reused only for identical immutable provider versions, preserving full closure proof without daily rehash of unchanged history.
- Minute previews are transient and are cleared on restore. Durable hourly originals and keograms remain part of the historical closure even after a related video's expiry.

## Alternatives and disposition

| Alternative | Disposition |
|---|---|
| Direct application-to-B2 archive/uploader | Not selected; conflicts with the accepted central local-first path for Weather and expands credential/delivery scope. |
| Separate media and backup application buckets | Not selected; conflicts with the fixed one-bucket contract. Enforced independent archive authorization is required instead. |
| Daily logical dump only | Rejected as a substitute for base/WAL PITR and exact stored application state. May be supplementary portability work if separately justified. |
| Treat central size/sample success as joint recovery | Rejected: one missing or corrupt unsampled referenced original can make the service unrecoverable. |
| Recompute narratives/vectors during restore | Rejected: historical model route or weights may be unavailable, and output would not reproduce the prior canonical state. |
| Count transient raw images or DB replicas as retained media backup | Rejected: neither preserves all durable media classes independently through their required lifecycle. |

## Capacity and timing contract

Source schedules are admission inputs, not Weather guarantees. The current full Tier-B window starts at 06:15 UTC and stops at 08:00 UTC with an 8 MiB/s ceiling and NAS gates. Its theoretical transfer ceiling is 50,400 MiB (49.22 GiB) across the shared window before listing, pauses, other buckets and verification. Weather receives no implied allocation of that capacity.

The current frequent-WAL profile has a 256 MiB per-run transfer cap on a 15-minute cadence. A healthy four-run hour has a theoretical 1 GiB transfer ceiling before contention and skips. The admitted backlog must drain faster than new generation; the cadence does not bound recovery age while NAS/archive/base prerequisites fail.

Admission must measure daily/new object bytes and counts by class, total retained/listing footprint, base/WAL growth, preview hide churn, per-version verification costs and catch-up after outages. Full first-copy and first-restore workloads run only on the approved bounded cluster path. Never download/hash the retained archive on the Mac as a shortcut.

Provisional targets from requirements remain database offsite age under 30 minutes and media/joint age under 36 hours. They are not accepted SLOs. A usable base, contiguous WAL, exact object versions and verification must all fit those targets. RTO remains unqualified until an isolated B2-only drill measures configuration bootstrap, transfer, database replay and product validation.

## Open research that prevents closure

1. **Per-class copy coverage:** database primary + local physical archive + offsite archive can form the intended three-copy topology. Durable media currently has only two named copies. Select a supported central-owner third-copy/two-media disposition or obtain the exact scoped owner exception; do not silently change the one canonical bucket or invent compliance.
2. **Independent bootstrap:** verify recovery identities, decryption keys, image/config availability and operator access without original Mac/NAS/DB. Existing encrypted exports referencing a Mac age key do not prove this.
3. **Full closure verification integration:** specify the admitted independent-reader verifier, receipt custody/version IDs, resume/budget policy and how central object manifests feed the application checkpoint. The algorithm is selected but the supported job/admission binding is not yet qualified.
4. **Capacity and lifecycle:** admit Weather's retained counts/bytes and copy/hash budgets; prove preview churn does not routinely trip the global hide guard; coordinate PITR pins and hidden-version lifecycle.
5. **Barman/gateway compatibility:** D08 must support real archive, list, multipart, retention and independent restore operations under exact roles. No bucket-wide credential may bypass the boundary.
6. **Measured RPO/RTO:** select achievable objectives from the admitted workload and isolated results; preserve honest NAS/Internet/archive failure states.

No retention change, additional copy, key creation or new workload was executed to close these questions.

## Required implementation acceptance

WX-28 proves actual base/WAL/archive identity and gateway behavior. WX-29 proves both custody enrollments and exact version protection using the independent reader. WX-30 proves checkpoint binding, every-object hash closure, manifest protection and GC/PITR races. WX-32 proves isolated B2-only restoration with original storage and inference blocked, then validates canonical weather queries, real video playback, private-original denial, restored FKs, editorial corrections and stored-vector search.

Failure cases include a missing WAL segment, wrong timeline, incomplete base, absent or mismatched original, unpublished/private asset confusion, stale manifest, hidden historical version, corrected editorial revision and expired model route. Any mismatch prevents joint-success reporting. Restore into a new archive identity with schedulers/retention disabled until validation completes.

Rollback of enrollment/configuration must retain every already-written offsite version and its receipts. Do not retire an old recovery identity or namespace until all retained checkpoints using it are either independently migratable and verified or legitimately outside their approved retention window. This ADR does not authorize credential revocation.

## Evidence

E01: private initial fleet/operator baseline, evidence limits in the decision register. E02: current central provider metadata and pinned source verifier/custody review, retained in the private owner issue with a public receipt hash. Neither is a Weather restore receipt. The service has not been deployed; all implementation gates above remain pending.

