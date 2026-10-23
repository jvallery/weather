# Codex first assignment — research, decisions, and filed implementation backlog

Use the existing public **`jvallery/weather`** repository. The owner has created it and published this handoff. Preserve existing work and complete its configuration; do not create a second repository or put the application in Sentinel.

Read [requirements.md](requirements.md) in full. It is the authoritative requirements document, version 3.0, dated 2026-10-23. Its R01–R28 register defines required outcomes; D01–D16 defines the decisions to resolve. The [candidate graph](implementation/backlog-candidate.json) is a starting decomposition, not an implemented system or filed issue list.

## Assignment

**Research and validate the live environment, make evidence-backed design decisions, then file the complete GitHub implementation backlog with native dependencies and a verified machine-readable graph.** Application implementation follows this pass.

Proceed with read-only inventory and approved bounded capability experiments using established owner workflows. Do not change production network paths, GPU allocation, shared storage/provider configuration, backup retention or unrelated workloads as an incidental research step. Resolve routine design choices from evidence. If a material requirement conflicts with available capability or required access is missing, complete the unblocked work and document the precise blocker, responsible owner, evidence needed and dependent issues.

## Investigations to complete

1. **Repository and delivery:** namespace/ownership, source license, public/private configuration split, repository settings and independent local CI/CD/registry/promotion design.
2. **Cameras and time:** actual native snapshot API behavior, sustainable cadence, timestamps/uncertainty, private site configuration, civil-twilight boundaries and exact 120-second combined frame allocation.
3. **NFS staging:** owner-managed Obsidian grant/PVC, capacity and mount durability, writer fencing, atomic chunk bundles, bounded failure/backpressure and cleanup eligibility.
4. **Tempest UDP:** read the Verdify reference and official protocol; select/prove the actual k3s network path. The ESP32 reference intentionally omits some packets and is not a complete archival ingestor. Define all-received-data semantics, exact raw bytes, every observation row, deduplication, durable spool and replay freshness.
5. **CNPG:** reconcile installed operator/plugin/PG/images/extensions and storage admission; select dedicated/shared infrastructure, HA/synchronous policy, native partitions versus TimescaleDB, full-history retention and capacity. Do not treat Verdify's retired CNPG rehearsal as an active template.
6. **Garage and access:** one isolated bucket for media and DB backups; select tier, capacity and actual durability. Native Garage keys are bucket-scoped. Select an enforcing boundary for media versus backup prefixes and qualify Barman operations; restore authorization cannot depend on the failed weather database.
7. **GPU co-tenancy:** inventory all nine devices and actual admission/owners, then propose a measured shared stage/resource envelope or qualified CPU baseline. Investigate existing assignments rather than assuming an assigned card cannot share; preserve resident services and use supported allocation mechanisms.
8. **Rendering:** qualify pinned current FFmpeg, interpolation-before-exposure profiles, actual-timestamp reconstruction, exact track/movie timescales, posters and CPU publication deadline.
9. **Video analysis:** freshly qualify `llm.primary.longctx.mm` with temporal-order controls and real completed-video input; define media/context limits, evidence schema, grounding and bounded degradation. Image-input success alone is insufficient.
10. **Embeddings/search:** requalify `embed.main` and its expected 2,560-dimensional contract, select exact search or compatible pgvector ANN representation, evaluate typed filters/recall and versioned reindex/restore behavior.
11. **UI/API/delivery:** choose Astro-plus-API or an integrated framework, match current `vallery.net` assets, define historical/current/search APIs, same-origin media/range/download semantics and optional plan-compliant Cloudflare use.
12. **Recovery/operations:** Barman base/WAL in the same Garage bucket, central full and exact-WAL enrollment, joint media/database checkpoint, coordinated retention, independent B2-only restore, realistic RPO/RTO and acceptance/SLOs.

## Required outputs

- Updated sanitized requirements and an ADR for each D01–D16 decision, with rationale, alternatives, observed evidence, source/version/time, resource/failure implications and implementation qualification gates.
- Private operational receipts stored in the established private system, referenced publicly only through safe summaries/opaque IDs.
- Repository configuration appropriate to planning: license, contribution/security guidance, issue templates and relevant settings. Pipeline/platform implementation remains issue-backed work.
- A full set of **real implementation issues**, refined from the 35 candidate keys where useful. Each issue states scope, R/ADR mapping, owner prerequisites, acceptance evidence, tests, operational behavior and rollout/rollback.
- Native GitHub **blocked-by** links, actual issue URLs/IDs, `docs/implementation/backlog.md`, an authoritative machine-readable DAG and readable diagrams.
- Validation that every requirement is covered, all dependency targets exist, no cycles/self-edges occur, optional GPU/CDN branches are selected correctly, owner-repository prerequisites are linked, and native dependencies match the committed graph.
- A read-back report of created/updated issues and a clear first ready implementation set.

Use stable keys to reconcile retries and prevent duplicate issues. Never fabricate issue numbers, infer API object IDs from issue numbers, or call the candidate graph a filed backlog. Native dependency capabilities are documented in the requirements sources; verify the installed CLI/API support.

## Completion report

Report **decisions complete**, **research blocked**, and **implementation pending** separately. Include the selected architecture, GPU proposal, UDP path, database/search design, shared-bucket authorization/durability decision, framework/CDN choice, recovery objectives and critical path.

Phase 1 is incomplete if a material architecture decision is unresolved or the promised issues/dependency graph have not actually been filed and verified. A selected design with a mandatory later implementation test is not a fabricated passing test; record that gate explicitly. Do not claim deployment, fresh data, working backups or production readiness from documentation alone.
