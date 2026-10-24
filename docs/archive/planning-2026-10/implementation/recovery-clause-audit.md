# Backup, custody and recovery requirements audit

The [source-bound ledger](recovery-clause-audit.json) preserves every section 15 block, all checkpoint inventory items and all eight failure-domain rows. The mapped acceptance below complements the recovery schema and canonicalization examples; neither synthetic schemas nor successful copy receipts prove recoverability.

## §15.1 — WX-28, WX-32

- Use installed compatible CNPG Barman Cloud Plugin with Weather-specific ObjectStore, archive identity, credentials, schedule, recovery window and monitoring; qualify version-specific serverName placement, not copied legacy syntax.
- Preserve usable physical base plus contiguous WAL through each supported target. Logical exports are supplemental; monitor throughput/lag/errors/base age/WAL free space and admitted archive outage budget, never manually delete unarchived WAL.
- Give each new/restored cluster a new archive identity; preserve archive-empty safety and isolate restore from public traffic and all application/retention jobs until validation.

## §15.2 — WX-29

- Enroll the one Weather Garage bucket through storage-owner workflows in both full and exact-WAL custody; no separate application B2 uploader or NAS media export.
- Full coverage includes videos/posters/keograms/maps/manifests, private hourly originals, sanitized comparison images, retained previews, immutable metadata/release/recovery records and bases. Frequent WAL enrollment follows each exact archive-server incarnation.
- Revalidate actual schedules/caps and admit daily bytes/object counts/base/WAL growth/listing/verification/backlog/lifecycle budgets and restore authority; historical schedules do not confer Weather coverage.
- Keep B2 credentials in central custody. Generic size/sample mirror checks cannot prove complete Weather closure; supplement with full application hash/identity verification.
- Backup failures alert and age protection while ordinary publication continues unless an explicit capacity/local-durability policy requires stopping.

## §15.3 — WX-30, WX-32

- Bind checkpoint to DB incarnation/base/timeline/unambiguous replay target and transactionally captured catalog generation; independent worker wall clocks are not atomic binding.
- Enumerate every retained referenced immutable object/hash including media/poster/keogram/sidecar/hourly/comparison, historical metadata, day/hour FKs and editorial/event revisions; exact set equality, not counts alone.
- Bind application/schema/PG/extensions and embedding profile/generation, secret/config identities without values, destination versions, verification time/custody and missing items.
- Offsite-recoverable requires usable base, contiguous WAL, every required exact-version object and manifest independently verified. Discover manifest without original DB/Garage/NAS.
- Restore highest verified common checkpoint; newer DB-only telemetry may be recovered only with missing media explicitly withheld/reconciled. Track DB-only, media and joint ages separately; frequent WAL does not improve uncopied media RPO.

## §15.4 — WX-28, WX-29, WX-30, WX-32

- Retention protects all supported PITR references, including currently tombstoned objects and discoverable/readable B2 versions. Do not assume Garage versioning/object lock.
- Independently recover config/images/credentials/secrets/decryption keys and broker authorization through existing custody without querying failed Weather DB; Secret names alone are not recoverable credentials.
- Media-serving authority cannot read backup/recovery identities or DB archives. Test independent recovery access and encryption dependencies without exposing secret values.
- Restore observations/receipts, narratives/metadata, full vectors/profiles/generation, jobs/outbox, tombstones and hashes with extensions installed; ANN rebuild may use restored vectors, never require AI or historical model availability.

## §15.5 — WX-29, WX-30, WX-32, WX-34

- Finalize numeric objectives from admitted topology/cadence/budgets and measured restore throughput; distinguish synchronous DB durability, local object loss, offsite telemetry/media ages and whole-service RTO. Under-30-minute DB and under-36-hour media/joint figures remain proposals.
- Perform isolated B2-only restore with independent identity and no original CNPG/Garage/Obsidian access; restore DB/media/sidecars/posters/visibility/weather/vectors/profiles/config and verify hashes plus actual playback, with inference unavailable.
- Assign restored cluster new archive identity before operation; record measured RPO/RTO, limits and exact app/image/schema generation.
- Inject newer WAL than media, absent media, missing WAL, PITR-required tombstone and expired model route; incomplete checkpoints cannot be selected as successful.
- Inject missing hourly original, mismatched still/day/hour FK, private/public confusion, corrected human text with pending embeddings and video expiry before five-year still expiry. Restore original bytes and prove public origin denial.
- Exclude obsolete minute previews from historical closure under the selected D15 policy, clear restored pointers and resume only newly eligible captures. Never omit durable hourly/keogram assets; central full custody still budgets preview churn.

## §15.6 — WX-31, WX-32, WX-33, WX-34

- Pod/node failure reconciles durable jobs without duplicate publication and uses only remaining admitted replicas; CNPG failover spools telemetry and publication waits for safe transaction.
- NFS failure produces visible capture gaps and bounded input-dependent render failure; NAS-backed Garage block failure shows unavailable uncached media even if metadata health is green, preserving DB history.
- Archive failure tracks backlog/free space and protects DB within approved outage budget; inference failure preserves weather/media/typed browsing with explicit analysis/search degradation.
- B2 failure ages offsite protection while primary service continues under retention/capacity policy; entire-site loss recovers latest independently verified checkpoint plus configuration/secrets.

## Evidence boundaries and unresolved mechanisms

D15/E02 records the inspected central custody policy and schedules, not Weather admission. D07/E10 records Garage build/settings/storage, not acknowledged object survival under power loss. Existing recovery contracts specify exact-version complete closure, database binding and independent verification, but central base/WAL/bootstrap ledger formats and trusted discovery remain unfinished.

The existing D15 decision clears transient preview pointers on restore and excludes obsolete previews from the historical application closure; central full-bucket copying still accounts for their retained churn. Durable hourly originals/comparison images/keograms remain mandatory even after video expiry. This distinction resolves the requirement’s policy choice without claiming an implemented restore.

Material planning gaps remain: supported third-copy/two-media disposition for durable media, Mac/NAS/DB-independent credential/config/image bootstrap, exact provider-version protection and complete verification integration, admitted transfer/capacity budgets, numeric RPO/RTO and public-route playback after recovery. Owner prerequisites do not constitute grants. No provider configuration, key, retention policy, archive or restore workload was changed. Existing graph edges remain sufficient for this mapping.
