# Storage, publication and retention audit

The [source-bound ledger](storage-clause-audit.json) preserves every prose/list/table/code block in §§11.1–11.4. It retains every logical key area and every identity/action row. Producer assignments below require actual acceptance, not just a linked implementation issue. WX-33 integrates denial/consistency checks and WX-32 proves independent recovery.

## §11.1 — WX-06, WX-29

- One private owner-provisioned Weather bucket includes every named media/image/metadata/recovery/database class; no dense raw staging, borrowed credentials or another application bucket.
- Revalidate tier policy/current state; one bucket occupies one actual tier and prefixes do not create different physical durability.
- Replace illustrative 630 MB/camera, 1.89 GB/cycle and annual payload estimate with measured rates plus all class/revision/backup/replica/growth costs.
- Class-specific video/poster/keogram, five-year hourly/comparison and preview horizons require recovery policy before deletion.
- NAS-backed uploads and uncached reads can fail despite metadata health/CDN cache; distinguish DB/application HA from capture/media availability.

## §11.2 — WX-07, WX-19, WX-28, WX-32, WX-37, WX-38

- Verify installed Garage native bucket/key scope and compatibility; object key/prefix is not an object credential, enforced IAM policy, versioning or object lock.
- Preserve every logical area in the example while implementing actual broker isolation; public/origin roles have no bucket-wide native credential or bypass.
- Enforce all nine identity rows: browser, origin, publisher, image worker, retention, archive, recovery, central custody and administrator with exact least privilege.
- Barman must use qualified S3 operations: pagination/list, GET/HEAD/PUT, multipart/abort/retry/WAL checks, deletion and restore; media upload API alone is insufficient.
- Negative tests include encoded/ambiguous paths, copy source, mixed-class delete batch, multipart IDs/parts, list-prefix escape and lifecycle/admin escalation.
- If existing gateway is insufficient, owner prerequisite supplies enforcement; never silently split buckets, widen origin keys or hide backups behind obscure names.
- Independent archive/restore scope/config/credentials survive Weather DB failure; media projection outage must not expose backup hierarchy.
- Private originals denied to origin/embed/anonymous routes including parent/direct/stale-pointer/copy paths; public derivation requires validated transformation, not class renaming.

## §11.3 — WX-06, WX-07, WX-17, WX-18

- Qualify exact Garage build/configuration plus NAS/NFS flush and accepted local-loss window; visibility/hash readback alone does not authorize cleanup.
- Seal/hash/pin immutable input and current generation before work; validate all mandatory outputs and initial sanitized sidecar even with pending AI/incomplete weather.
- Upload immutable hashes through scoped roles, verify qualified bytes/size/content and safely abort own multipart remnants; ETag is not SHA-256.
- One DB transaction CASes generation and commits asset/metadata references, publication state/outbox/receipt only after uploads are complete.
- Origin readback is required; video pin release cannot discard hourly/keogram obligations.
- DB failure after upload leaves an unlisted orphan with safe reconciliation/grace; no stale worker overwrite or mutable retry identity.
- Enrichment uses new immutable sidecar and DB revision while media hash stays fixed; detect/regenerate stale or absent exports.
- Image/keogram uses same publication discipline; private original catalog is not public, preview needs delay, per-product degradation and corrections never overwrite published bytes.

## §11.4 — WX-16, WX-18, WX-30, WX-43

- Automatic poster comes from completed validated video with actual aspect/privacy and frame/PTS/source mapping, dimensions/MIME/bytes/hash.
- Select genuine representative frame without gap slate/obstruction/black when better exists; never swap another day into a dark valid night. Responsive JPEG/WebP and compatible fallback; extras optional.
- Posters are first-class same-origin/versioned/backup/recovery assets; unavailable uses labeled UI placeholder, never generative snapshot.
- Private manual choice uses eligible retained observed original or decoded same-video/segment frame with recorded distinction, camera/quality/masks and approved new derivative; never reveal private bytes/key or promise arbitrary raw exposure after cleanup.
- Retention starts with tombstone/public update then grace and all client/job/PITR/custody reference checks; preserve expired-date metadata.
- Urgent withdrawal requires controlled origin/cache purge; DB flag cannot recall cached bytes.
- Independent lifecycles preserve five-year stills despite video expiry, bounded preview rotation, editorial audit and DB/WAL/recovery references.
- Restore reevaluates preview age/withdrawal; masks/withdrawals propagate through all public derivative classes without making private original public.

## Evidence and unresolved mechanisms

D07/E10 records the running custom Garage binary/digest, mounted settings and storage binding; it does not qualify acknowledged data survival. D08 specifies the enforcing gateway and independent archive authority; atomic immutable-create and Barman compatibility still need owner proof. D15/E02 records central custody settings; it does not establish complete Weather recovery closure or bootstrap. Preserve those distinctions in each consumer issue.

The publication and compact-promotion contracts define transactional ownership/receipts, but application and object-store writes remain separate operations. Retrying cannot rely on HEAD-then-unconditional-PUT for immutability. Retention cannot infer authority from a hash, tombstone, expiry, missing live worker or copied receipt alone. Tests must resolve the current authoritative generation and complete reference set and fail closed when protection evidence is unavailable.

Current plan has no generic proof that prefix isolation, Barman compatibility, actual local durability, physical-copy coverage or cache recall work. Complete exact gateway/provider mechanisms, accepted local-loss/capacity policy, independent custody formats and bounded withdrawal before closing those material decisions. A successful synthetic schema or object read is not a substitute. No bucket/key/lifecycle, gateway, upload or deletion was changed during this audit.
