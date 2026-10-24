# E02: central custody capability and limits

Observed 2026-10-23 at 20:03:51 UTC. Private metadata receipt is retained on [OWN-CUSTODY](https://github.com/jvallery/storage-infra/issues/1109), SHA-256 `558de96c6ba37e0c38fb8d6e7d6d2b0df7ec0a7796af6221764007f2664067bc`. No credential, token, hardware identity or private object listing is included here. Operations were read-only: existing-identity authorization and exact destination policy/capability queries. No provider settings or objects changed.

Source review pinned storage-infra revision `adeceb1e60dff8cf5bf96e3170eee2bc615b4a9b`: central custody inventory/runbook, copy/verification implementation and encrypted-key recovery README. Source intent, current provider metadata and historical qualification are distinct.

## Confirmed now

- The existing central destination is private and uses provider-managed AES256 encryption.
- Object Lock is enabled; default retention is governance for 45 days.
- Hidden versions have a 45-day lifecycle interval; there is no automatic upload-age hide rule in the observed policy.
- Writer and reader are scoped to the same one destination and custody prefix.
- The writer can upload and extend retention, but has no version-delete, governance-bypass, bucket-administration or key-management capability.
- The reader has read/list/retention-inspection capabilities and no write capability.

These are metadata facts, not a fresh destructive-denial test. The private source receipt records a historical provisioning test, but that is not described as repeated during this audit. Governance protection is relative to the scoped writer; management authority can have stronger powers. Do not call it protection against every possible administrator.

## What the current copy verifier proves

The source verifier requires matching sizes for the unchanged source set and destination listing, then checks a bounded SHA-256 sample. Full-profile manifests require an Object Lock readback before advancing the latest pointer. The WAL profile samples content on its own cadence. This is useful central custody evidence, but it does not satisfy Weather's full immutable-object hash closure by itself.

Select an additional Weather checkpoint-verification stage under the central owner workflow. It reads through the independent recovery identity and binds exact destination version IDs, lengths and SHA-256 to the application's expected immutable hashes. Reuse a prior verified receipt only for the identical provider version/hash identity and still-valid retention/reference policy; do not infer hash equality from name/size/ETag or reuse a receipt for a newly uploaded version. The whole checkpoint closure must resolve to verified receipts. Bounded batches, resumable progress and a stable checkpoint ID avoid rehashing unchanged history on every daily checkpoint without weakening first-copy verification.

Missing bytes, changed versions, incomplete WAL, absent retention evidence or an unverified manifest keeps the checkpoint incomplete. It must not advance the joint-recovery success signal. This stage does not create a separate Weather B2 uploader or distribute B2 credentials to the public application.

## Remaining per-class custody question

| Class | Named intended copies | Remaining proof/design |
|---|---|---|
| Database history, editorial state and stored vectors | Primary CNPG; local physical base/WAL; central offsite base/WAL | Three-copy topology is plausible; require actual usable chains, independence, capacity, immutable versions and restore. Replicas alone are not backups. |
| Durable videos, posters, sidecars, keograms and selected originals | Primary Garage; central B2 mirror | Third-copy/two-media disposition under STD-DR-01 remains unresolved. Dense raw input and transient caches do not count as retained independent copies. |
| Deployment/recovery configuration and identities | Source/central encrypted recovery exports | Independently available decryption/identity bootstrap without original Mac/NAS still needs proof. |
| Minute previews and dense raw captures | Explicit bounded transient lifecycle | Document exclusion and honest loss behavior; do not accidentally apply it to retained hourly originals or keograms. |

The one canonical private application bucket and central local-then-offsite custody remain fixed. Do not silently add a second canonical bucket, independent application uploader, new credentials or retention exception. OWN-CUSTODY must provide a supported per-class disposition or an exact unresolved owner decision before D15 is considered closed.

STD-DR-01 does not itself require three distinct physical sites or three distinct failure domains. The owner must demonstrate three usable retained copies, the required media separation, and offsite immutable/offline protection. Multiple destination versions may contribute to a qualified recovery design, but a version count alone does not prove an additional usable backup; identify the recoverable set, retention, independence from overwrite/corruption and restore method explicitly.

## Retention interactions

Five-year original retention refers to the application's retained original and required comparison derivatives. A 45-day default governance lock is not a five-year immutable guarantee. Source retention, mirror hide behavior, historical version lookup and the supported PITR window must be coordinated explicitly. A five-year current object need not expire because its initial lock period ends, but a later source deletion/hide changes the recoverability window.

A large batch of preview expiry or old-media cleanup can trip the central hide guard. Weather's bounded preview lifecycle therefore needs copy-cost and hide-rate admission; no task may raise the global guard to make a cleanup pass. Keep retained historical object versions pinned/discoverable for each supported checkpoint/PITR target.

The private key-recovery README instructs decryption with the operator age identity. This is evidence of encrypted exports, not proof of an independently recoverable key after Mac loss. No key was printed, copied, rotated or revoked in this audit.

## Decision impact

D15 can select the existing central 45-day governance destination and scoped identities as the supported integration target, subject to actual Weather enrollment and object-level qualification. The earlier unknown-default-policy question is resolved. Third-copy coverage for durable media, cold bootstrap, full hash closure, capacity/RPO/RTO and isolated application restore remain open. Standards rows remain unverified for the undeployed Weather service.

