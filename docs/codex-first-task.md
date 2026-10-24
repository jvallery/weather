# Codex continuation — execute the filed Weather plan

Use the existing public **`jvallery/weather`** repository. It is already cloned and its planning collateral is published. Preserve existing work and verify the current branch/remote before editing; do not create a second repository or put the application in Sentinel.

Read [requirements.md](requirements.md) in full. It is the authoritative requirements document, **version 4.0**, dated 2026-10-23. Its **R01–R38** register defines required outcomes; **D01–D16** defines the decisions to resolve. Read the [accepted Q01–Q16 decisions](decisions/2026-10-23-product-scope.md) and the [source-pinned landscape review](research/open-source-landscape.md). The [filed backlog](implementation/backlog.json) is authoritative for actual issue IDs and native dependencies; the [candidate graph](implementation/backlog-candidate.json) is historical decomposition only. Do not regenerate issues from the candidate graph.

The owner chose an **independent, fleet-first application**, with specific upstream implementation references in requirements §20.1. Do not reopen the questionnaire or make application-fork experiments a prerequisite. R29–R37 are selected initial features. R38/WX-45 is the accepted combined east/west skycam follow-on; WX-46 reserves later visual-index research. Both are already filed (#46 and #47), and neither blocks the first release.

## Planning closeout

The architecture-level planning audit is complete. Start from the [execution-wave handoff](implementation/execution-handoff.md) and live issue states. The review instructions below explain the completed planning boundary; do not repeat this pass or reopen settled choices. Detailed discovery belongs to the assigned execution issue. Actual implementation proceeds when requested under its owner/dependency and estate resource constraints.

## Archived review assignment

**Finish the architecture consistency review and publish the execution-wave handoff.** Jason directed us to stop deep investigation: design and architecture must align with current fleet standards, while mechanism discovery and implementation details belong to the execution sprint. Do not perform more provider, fencing, CPU/model, recovery or compatibility investigation as a planning prerequisite.

Check accepted scope, component/data boundaries, owner lanes, standards, requirement coverage and dependency order. Carry unresolved technical details into the existing execution issues with an owner, discovery outcome and dependent acceptance gate. Do not start application implementation in this review or interpret historical planning-blocker language as overriding this direction.

## Current authoritative state and next work

The [planning closure inventory](implementation/planning-closure.md) contains the finite P1–P7 finish list. Use it with the individual ADRs; this handoff is an entry point, not a competing status ledger. Inspect current GitHub bodies before edits, retain private prior bodies, and verify exact readback. Preserve unrelated checkouts and owner delivery holds.

Established choices and collateral:

- Cloudflare Tunnel selected for video and downloads; do not reopen direct ingress or paid-provider investigation. Preserve required interpolation, descriptions and semantic search in the initial scope.
- MIT for original code with compatible permissive reuse; separate review before copyleft imports. Do not re-ask the license questionnaire.
- React + TypeScript / React Router SSR, separate Python API/workers, direct frontend/backend version and archive evidence, and 13 rendered responsive wireframes. Full lock/image/runtime qualification remains future work.
- Dedicated CNPG, native time partitions and exact-vector baseline; source-time/civil-slot, capture/publication, search/editorial, preview and recovery contracts. Schemas/vectors specify behavior without proving runtime correctness.
- All 46 Weather work keys plus WX-00 and 14 owner issues exist. The [combined native readback](implementation/embed-order-graph-verification.json) verifies 123 edges including owner-side dependencies; all 42 required initial items are in rollout closure. This receipt has exact observation/input hashes and must not be represented as final semantic issue-body approval.
- Embed order is WX-44 card → OWN-EMBED WordPress integration → WX-33 combined acceptance. The current-weather card does not wait for camera previews. OWN-CUSTODY depends on OWN-OBJECT. Owner readiness is distinct from downstream application acceptance as specified in [owner prerequisites](implementation/owner-prerequisites.md).
- E01's original bytes are retained and hash-verified in private OWN-CI, with sanitized public references. E02–E12 and subsequent source receipts have explicit evidence limits; old health/resource samples are not current grants.

Before asking product questions, read the [owner-input reconciliation](decisions/2026-10-23-product-scope.md#remaining-owner-input-and-decisions-not-to-reopen). Exact location disclosure, initial AI/interpolation scope and provisional recovery targets are already constrained; all three full camera views without visual masks are approved in D02. Homepage placement is approved in D13; do not reopen either decision.

Remaining planning work is a final consistency check and concise execution-wave handoff. P1–P6 describe sprint discovery/owner integration, not additional planning gates. WX-00 closure does not claim those tasks are implemented or qualified. Execution begins with ready owner discovery and Weather foundations under the filed graph; mutation/build still requires the relevant implemented owner interface and normal estate resource rules.

No new product questionnaire is pending. Do not repeat research or broaden the plan to resolve every unknown. Distinguish planning completion from execution readiness of each individual task and from release acceptance.

## Execution discovery and acceptance checklist

The following detailed checks belong to their execution issues. During this review verify assignment and consistency, not that these checks have already passed.

1. **Repository and delivery:** namespace/ownership, the selected MIT/permissive reuse policy and upstream obligations, public/private configuration split, repository settings and independent local CI/CD/registry/promotion design. Document fleet customization without building a general multi-tenant platform.
2. **Cameras and time:** actual native snapshot API behavior, sustainable cadence, timestamps/uncertainty, private site configuration, civil-twilight boundaries and exact 120-second combined frame allocation. Qualify regions/bins for all-three-camera day/night keograms, completed-hour still selection, separate calendar-day/hour FKs and solar cycles, and 23/24/25-hour DST behavior.
3. **NFS staging:** owner-managed Obsidian grant/PVC, capacity and mount durability, writer fencing, atomic chunk bundles, bounded failure/backpressure and cleanup eligibility. Define hourly candidate/promotion pin transfer and keogram extraction before dense input deletion. Qualify safe sealed-input access for approximately one-minute previews without expanding camera-only collector egress.
4. **Tempest UDP:** read the Verdify reference and official protocol; select/prove the actual k3s network path. The ESP32 reference intentionally omits some packets and is not a complete archival ingestor. Define all-received-data semantics, exact raw bytes, every observation row, deduplication, durable spool and replay freshness.
5. **CNPG:** reconcile installed operator/plugin/PG/images/extensions and storage admission; select dedicated/shared infrastructure, HA/synchronous policy, native partitions versus TimescaleDB, full-history retention and capacity. Add enforced day/hour/camera/source relationships, events, effective editorial revisions and asset classes. Do not treat Verdify's retired CNPG rehearsal as an active template.
6. **Garage and access:** one isolated bucket for public derivatives/videos, private hourly originals and DB backups; select tier, capacity and actual durability. Native Garage keys are bucket-scoped. Select an enforcing boundary for all classes and qualify Barman operations; restore authorization cannot depend on the failed weather database. Include the selected five-year hourly-original/comparison-image default, bounded current previews and ten-minute public image embargo; separate lifecycles from video retention.
7. **GPU co-tenancy:** inventory all nine devices and actual admission/owners, then propose a measured shared stage/resource envelope or qualified CPU baseline. Investigate existing assignments rather than assuming an assigned card cannot share; preserve resident services and use supported allocation mechanisms.
8. **Rendering:** qualify pinned current FFmpeg, interpolation-before-exposure profiles, actual-timestamp reconstruction, exact track/movie timescales, posters and CPU publication deadline.
9. **Video analysis:** freshly qualify `llm.primary.longctx.mm` with temporal-order controls and real completed-video input; define media/context limits, evidence schema, grounding and bounded degradation. Image-input success alone is insufficient.
10. **Embeddings/search:** requalify `embed.main` and its expected 2,560-dimensional contract, select exact text-vector search or compatible pgvector ANN representation, evaluate measured numeric filters/coverage/recall and editorial-aware reindex/restore behavior. Numeric-only queries must work offline from inference. A future visual index is a separate deferred investigation, not an initial model gate.
11. **UI/API/delivery:** apply the owner-approved React + TypeScript / React Router Framework Mode design with a separate Python API, match current `vallery.net` assets, and define per-camera linked plots, hourly comparisons, keograms, delayed previews, bookmarks/in-site feed, local almanac and measured search. Qualify private operator corrections/tags/favorites/poster controls and embed-route framing/main-site owner integration. Preserve 15-second current conditions, complete history exports, same-origin media/ranges/downloads and optional qualified Cloudflare use.
12. **Recovery/operations:** Barman base/WAL in the same Garage bucket, central full and exact-WAL enrollment, joint media/database checkpoint, all durable image/day/editorial references, bounded preview recovery policy, coordinated retention, independent B2-only restore, realistic RPO/RTO and acceptance/SLOs. Treat local-only observations/astronomy and all received telemetry as fixed scope.

## Required outputs

- Updated sanitized requirements and an ADR for each D01–D16 decision, with rationale, alternatives, observed evidence, source/version/time, resource/failure implications and implementation qualification gates.
- Accepted-question-to-R/ADR/issue traceability and selected upstream-reference/reuse mapping; preserve the owner's answers while resolving implementation choices.
- Private operational receipts stored in the established private system, referenced publicly only through safe summaries/opaque IDs.
- Repository configuration appropriate to planning: license, contribution/security guidance, issue templates and relevant settings. Pipeline/platform implementation remains issue-backed work.
- A complete, consolidated set of the **46 already-filed implementation/follow-on issues**, refining their bodies where needed without duplicating keys. Preserve WX-01–WX-35 identities and the selected new outcomes WX-36–WX-46. Each issue states scope, R/ADR mapping, owner prerequisites, acceptance evidence, tests, operational behavior and rollout/rollback.
- Native GitHub **blocked-by** links, actual issue URLs/IDs, `docs/implementation/backlog.md`, an authoritative machine-readable DAG and readable diagrams.
- Validation that every requirement is covered, all dependency targets exist, no cycles/self-edges occur, optional GPU/CDN branches are selected correctly, owner-repository prerequisites are linked, and native dependencies match the committed graph. Every required initial issue must precede WX-35; WX-45/WX-46 must be outside its prerequisite closure.
- Initial-release and follow-on milestones/labels with explicit `phase` and `release_gate` metadata. Retain WX-45 as the accepted composite product and WX-46 as reserved visual-index research; do not mislabel either as already implemented or promote them into initial acceptance.
- A final read-back report of updated issues and a first eligible implementation set based on actual closed prerequisites. If none is ready, say so and identify the exact remaining condition.

Use stable keys to reconcile retries and prevent duplicate issues. Never fabricate issue numbers, infer API object IDs from issue numbers, or call the candidate graph a filed backlog. Native dependency capabilities are documented in the requirements sources; verify the installed CLI/API support.

## Completion report

Report **decisions complete**, **research blocked**, and **implementation pending** separately. Include the selected architecture, GPU proposal, UDP path, database/search design, shared-bucket authorization/durability decision, framework/CDN choice, recovery objectives and critical path.

Also report the camera-image lifecycle/FK design, delay enforcement, per-camera linked exploration, event/almanac/search contracts, private editorial boundary, main-site embed prerequisite and the recorded license/reuse choice. Initial rollout does not require resolved production layout for the later composite or a selected visual embedding model. Their filed issues must still contain concrete investigation scope, dependencies and acceptance criteria.

Phase 1 is incomplete if a material architecture decision is unresolved or the promised issues/dependency graph have not actually been filed and verified. A selected design with a mandatory later implementation test is not a fabricated passing test; record that gate explicitly. Do not claim deployment, fresh data, working backups or production readiness from documentation alone.
