# Section 6 acquisition audit

The [source-bound ledger](capture-clause-audit.json) preserves every prose/list/table/fenced block in §§6.1–6.11, including illustrative arithmetic and all capture, storage and image-product requirements. Grouped obligations below identify concrete producers; WX-33 integrates acceptance. This is design traceability, not complete whole-document review or runtime acceptance.

E06 proves three individual decoded native JPEGs, not cadence, sensor parity or timing trust. E08 proves generic NFS configuration, not an admitted Weather grant/fence. E10 proves the mounted Garage runtime/configuration, not accepted local-loss durability. These limitations remain despite schema checks and detailed designs.

## §6.1 — WX-10

Design dependencies: D02, OWN-CAM.

- Reconcile private inventory/firmware and exact JPEG geometry; distinguish single-frame proof from sustained-rate/native-mode qualification.
- Fixed channel-1 Digest snapshot API; no assumed Reolink limit, no management writes or changes to existing stream/stitch/exposure/NVR configuration.
- Validate helper semantics before reuse and keep source credentials private.

## §6.2 — WX-10

Design dependencies: D02, D03.

- Allowlisted camera/port and mounted scoped credentials; no redirects to another authority, prefer verified HTTPS or approved VLAN/Digest.
- Absolute monotonic slots with staggered phases, bounded pool and one in-flight request per camera; skip overdue slots, never catch-up bursts.
- Connect/read limits plus one total deadline covering negotiation/retry/body; bounded bytes and dimension/decode/signature/checksum validation reject HTML, truncation and slow trickle.
- Persist every attempt; transport backoff differs from slow auth-failure retries; bounded shutdown and atomic seals.

## §6.3 — WX-10

Design dependencies: D02, OWN-CAM.

- Commission each camera at 1 Hz through day/twilight/night: latency quantiles, bytes, geometry, fresh-frame rate, errors and existing NVR health.
- Enable 2 Hz only after actual refresh/capacity evidence; lower cadence is explicit revisioned degradation.
- Snapshot bandwidth example is not a guarantee of savings versus compressed video; no persistent RTSP decoder.

## §6.4 — WX-08, WX-10

Design dependencies: D02, D03.

- Retain slot/request/completion UTC, monotonic evidence, source estimate/basis, uncertainty, clock epoch and freshness evidence distinctly.
- Camera timestamp must be verified or midpoint/cache behavior calibrated; HTTP Date, arrival and mtime are not exposure time.
- Identical hashes do not alone establish staleness or authorize deletion.
- Collector does not contact NTP; wall-clock discontinuity creates new epoch/future mapping without slot reuse or interpolation across unexplained reversal.

## §6.5 — WX-10

Design dependencies: D03, OWN-NFS.

- Five-minute original-byte TAR/index bundles cut at boundaries; sole active writer, durable checkpoints and atomic full-bundle seal.
- Rename visibility is not power-loss durability; accepted tail-loss budget, complete-member repair and index/hash reconciliation are required.
- Preserve failed/skipped attempts and reserved control metadata; process sealed inputs only under pins.
- Reject traversal/links/devices/unknown or duplicate members, extension/name violations and bad bounds; plain TAR representation now has 19 checked shape fixtures.

## §6.6 — WX-09, WX-10

Design dependencies: D02, D03.

- Precompute/freeze several days of schedule; seal only after boundary, all relevant bounded attempts resolved and admitted grace elapsed.
- Validate source-assigned membership, attempts/accepted unique totals, coverage/discontinuities, hashes and boundary model.
- Clock corrections create immutable superseding revisions; never silently edit running-job inputs.

## §6.7 — WX-03, WX-10

Design dependencies: D03, OWN-NFS.

- Dedicated approved Obsidian HDD NFS grant, exact ownership/modes/capacity and allowed workload/node access; no unrelated share or Longhorn raw substitute.
- RWX permits placement but supplies no fencing; one admitted writer/camera with qualified lock, epoch and old-process stop/fence proof.
- Coordinator may use control APIs; collector camera-only network boundary remains; separate fenced cleanup role cannot delete newer generations.
- If old writer stop/fence is uncertain pause; test partition/reconnect/lock recovery/stale return/boundary rollover/append crash.
- NAS failure stops persistence, bounds pending memory, records gaps and never spills to unapproved disk; kernel-I/O watchdog and checkpoint reconstruction need qualification.

## §6.8 — WX-03, WX-18, WX-31

Design dependencies: D03, D16, OWN-NFS.

- Use measured p95 bytes/cadence, longest segment/backlog/scratch and 30% reserve; distinguish quota/PVC label/free NAS space and other consumers.
- Verify decimal-unit rate arithmetic; cleanup reduces occupancy, not lifetime write volume, and TAR does not reduce JPEG bytes.
- No full-day synthetic/uncompressed intermediate; preserve control reserve, per-camera accounting and healthy/recovery capacity.
- 70/85/90 watermarks require admitted usable budget; lower resume threshold and pressure policy cannot overwrite unpublished/active/pinned inputs or unrelated data.
- Forty-eight hours is escalation, not terminal-loss authorization; uncertain capacity stops payload writes with visible gaps.

## §6.9 — WX-18, WX-36, WX-37

Design dependencies: D03, D07, D15, D16.

- All mandatory video/poster validation, qualified Garage durability, DB publication receipt and origin readback plus all pins resolved before dense-source deletion.
- Hourly bytes/obligations and compact keogram input may transfer durably with hash/ownership receipt before releasing source pin; pending promotion is not archived success.
- Cross-boundary selection remains pinned; crash transfer may retain both copies but never leave neither protected; use same approved NFS grant, no alternate NVMe.
- Missing-slot decision is explicit; one-hour cleanup target never overrides holds; retain compact provenance/deletion receipts and retry idempotently.
- New capture proceeds independently under admitted budgets; AI uses completed media, and asynchronous B2 is not normally a raw hold unless local-loss qualification requires it.
- Extraction/archive failure preserves last evidence, reports degradation and invokes admitted backpressure; lease expiry is never deletion permission.

## §6.10 — WX-37, WX-08, WX-19, WX-40

Design dependencies: D02, D07, D08, D15, D16.

- Nearest qualified source-time midpoint selection; earlier time then ID tie-break; preferred 60-second offset otherwise flagged same-hour fallback or explicit missing.
- Finalize after hour closes plus sealed candidates/late-input deadline; register obligation before either cross-twilight segment cleans up; no whole-day retention or double segment ownership.
- Private immutable exact JPEG with size/hash/geometry/time/uncertainty/source and camera/day/hour FKs; durable receipt/transaction before releasing last original pin.
- Retries converge on one active selection; corrections preserve revision/audit/tombstone/grace and never overwrite different hashes.
- Sanitized/masked public derivatives only, useful responsive sizes and comparison dates/offsets/missing states; never expose native original or imply matching solar phase.
- Every comparison/poster/thumbnail obeys 600-second upper-bound embargo even for late-hour fallback; archive can finish privately first.
- Five-year selected original/comparison horizon is independent of shorter video life; retain needed FK/tombstone/recovery references; no cascade deletion.
- Capacity includes leap/DST hours, derivative/replica/offsite bytes; sparse archive is not authority to retain dense capture.

## §6.11 — WX-38, WX-19, WX-20

Design dependencies: D03, D13, D16.

- Three cameras, minute-level coalesced publication from existing sealed captures; no public request starts capture or RTSP.
- All routes enforce latest plausible exposure +600 seconds, including guessed URLs, aliases/thumbnails/HEAD/range/embed/prewarm; unknown/future/unsafe timing fails closed.
- Separate image worker, approved masking/sanitation, immutable derivative and atomic pointer; extra sealed-chunk age accepted rather than mutable-tail bypass.
- Preserve source/publication times, generation, policy/quality/uncertainty and current age; stale/placeholder bounds and short caches do not relabel old frames fresh.
- Outage recovery coalesces newest eligible capture rather than historical backlog; preview retention/custody separate from hourly originals.
- Controlled policy/timestamp correction withdraws/invalidate projections/caches; logical expiry never releases recovery-held physical bytes.

## Findings and next proof

The audit found a real representation mismatch: 128-character capture IDs could not satisfy the no-long-name TAR contract. The [capture contract](../contracts/capture-contract.md) now constrains attempt/path bounds and aligned payload offsets; its [19-fixture/TAR probe receipt](../contracts/capture-validation-receipt.json) is bounded structural evidence only. The [preview policy](../contracts/preview-policy-contract.md) separately resolves current-display cutoff, logical expiry and recovery-held physical bytes.

No required clause is considered runtime-accepted. Remaining material design qualification includes actual NFS grant/fence and durable compact-promotion receipt format, camera cadence/timing/privacy/regions, local object-loss policy, actual quotas/control reserve, and cache withdrawal behavior. The traceability here does not close those gaps by assigning them to an issue. Future acceptance must record exact admitted configuration, failure injection/outcome, source revision and retained private evidence without publishing images/credentials.
