# Weather architecture and execution-wave handoff

## Review boundary

Jason directed this review to establish a consistent design aligned with current fleet standards, leaving detailed discovery and implementation to the execution sprint. This handoff preserves the full product scope. It does not claim that resources, pipelines, application behavior or recovery are deployed or qualified. Existing ADR warnings about unresolved mechanism details are execution obligations, not demands for further planning research.

## What we are building

An independent public Weather application combining complete received Tempest history with three-camera daytime/overnight timelapses, keograms, private hourly originals and public comparisons, delayed previews, linked weather plots, local almanac, events, AI descriptions, semantic/measured search and private editorial controls. The homepage gets a compact current-weather card after “What I am doing now.” All three camera views are approved without visual masks; original bytes remain private and the minimum ten-minute public-image eligibility remains enforced. The east/west composite is follow-on WX-45; visual-index research is deferred WX-46.

## Architecture and fleet alignment

| Boundary | Selected design | Owner / execution discovery |
|---|---|---|
| Web | React + TypeScript, React Router SSR/client navigation, scoped Query polling, local assets; Python owns API/auth/evidence | Weather; complete lock/image and browser qualification in WX-02/20/26/43/44 |
| Public delivery | Cloudflare Tunnel, same-origin API/media/downloads, no direct Garage URLs; initial no-store media and bounded origin authorization | Network OWN-EDGE + Weather WX-19; route/resource/stream verification in execution |
| Acquisition | Direct native camera snapshots; isolated Tempest UDP receiver, byte-preserving durable spool | Sentinel OWN-CAM, network OWN-UDP, agents OWN-L2; Weather WX-04/10/11 calibrate timing, cadence and attachment |
| Working storage | Owner-managed NFS staging, sealed inputs, writer epochs/fencing, bounded pressure and product pins | Storage OWN-NFS; WX-03/10/18 resolve admitted mount/fence details before use |
| Canonical data | Dedicated CNPG PostgreSQL, native time partitions, complete telemetry, catalog/FKs/outbox and exact-vector baseline | Storage OWN-DB; WX-05/08/12/23 qualify versions, storage and extension/profile compatibility |
| Objects | One private isolated Garage bucket, class-scoped gateway, immutable publication, separate originals/public/archive access | Storage OWN-OBJECT/GATEWAY; WX-06/07/17 resolve concurrency, durability and Barman operations before publication/cleanup |
| Media | Exact source-time geometry/duration, high interpolation and tested CPU fallback; GPU co-tenancy conditional | Weather WX-14/16; compute OWN-GPU and conditional WX-15 qualify an admitted stage without disturbing residents |
| Inference/search | Local completed-video analysis and text embeddings; versioned evidence/revisions, bounded requests, outage-independent measured browsing | Compute OWN-MODELS; WX-21–25/42 qualify actual profiles, limits and quality during execution |
| Recovery | Barman base/WAL in Garage, central full/WAL offsite custody, transaction-bound joint checkpoint and independent restore | Storage OWN-CUSTODY; WX-28–32 resolve copy/bootstrap details and measure restore; provisional DB <30m, media/joint <36h offsite age, RTO measured in sprint |
| Identity and deployment | Existing estate identity, explicit operator authorization; isolated untrusted CI, trusted digest promotion, private KSOPS/Argo overlays | Agents OWN-CI, Weather WX-01/02/34; no borrowed credentials or public secrets |
| Operations | Source-owned monitoring, distinct freshness/product/recovery signals, bounded failure behavior and retained rollback state | Monitoring OWN-OBS + Weather WX-31/33/35 |
| Parent embed | Weather child route then WordPress homepage integration, then combined acceptance | WX-44 → OWN-EMBED → WX-33; preserve narrow two-sided CSP |

The [standards matrix](standards-mapping.md) maps direct/shared/inherited estate obligations and their evidence; [owner prerequisites](owner-prerequisites.md) supplies exact issue links. Existing E01–E13 observations and ADR source references establish the fleet context, not current resource grants or runtime success. Revalidate scoped evidence before mutation, through each owner's existing supported path. Intensive work runs on the authorized cluster, not the Mac.

## Execution waves

Waves are coordination groups, not new global barriers. The [filed native graph](../../../implementation/backlog.json) controls task readiness. Start any task whose own prerequisites are satisfied; do not wait for unrelated tasks in its wave.

1. **Owner discovery and foundations:** launch the dependency-free owner issues for CI, NFS, database, objects/gateway, UDP/L2, cameras, edge, models/GPU and monitoring. OWN-CUSTODY follows OWN-OBJECT; OWN-EMBED follows WX-44. Complete WX-01/02 when OWN-CI is ready. Owner discovery may begin without a finished application; owner closure must supply a usable interface, not require downstream Weather acceptance.
2. **Acquisition and persistence:** WX-03–12 and WX-17 establish the admitted platform, schema, source clocks, sealed camera inputs, complete UDP capture and history. Independent camera and telemetry lanes proceed as their dependencies clear. Resolve fencing, spool and immutable-write details here.
3. **Products and public experience:** WX-13–16/19–20/36–44 add current/history APIs, required interpolation/renditions, keograms, hourly archive, previews, linked exploration, events/almanac, editor and card. Run model/search work WX-21–26 alongside ready product work. Discovery and focused acceptance belong to each issue; feature degradation must not remove required scope.
4. **Protection and integration:** WX-18/28–31 qualify cleanup, physical archive, offsite enrollment, joint checkpoints and monitoring. These may start earlier wherever the DAG permits. Complete OWN-EMBED after WX-44. Optional WX-15 GPU and WX-27 caching cannot become unselected release gates.
5. **Release proof and handoff:** WX-32 independent restore and WX-33 integrated browser/security/accessibility/quality evidence; WX-34 runbooks/delivery; WX-35 controlled initial rollout. No green CI-only completion claim. Preserve source, build, deployed, product and recovery evidence separately.
6. **Follow-on:** WX-45 composite after initial rollout; WX-46 visual-index investigation remains deferred.

## Critical path and first ready work

The fresh native readback verifies 123 edges across 46 Weather work issues and 14 owner prerequisites, with all 42 required initial issues in WX-35 closure. WX-45/46 are excluded. One longest dependency chain by node count is:

`WX-00 → WX-01 → WX-05 → WX-08 → WX-11 → WX-12 → WX-13 → WX-42 → WX-25 → WX-31 → WX-32 → WX-34 → WX-35`

This is an ordering result, not an elapsed-time estimate. Owner delivery duration and task sizes are unmeasured. After planning closure, dependency-free owner discovery is the first ready work; WX-01 and WX-02 become ready when OWN-CI is implemented and qualified. Do not treat an open issue as a running task or a resource grant. An execution agent blocked on one owner should take another ready issue rather than repeat the same investigation indefinitely.

## Acceptance and evidence map

Use [R/ADR/issue traceability](traceability.md), the [acceptance matrix](acceptance-matrix.md), [accepted Q01–Q16 decisions](../../../decisions/2026-10-23-product-scope.md), individual issue criteria and their linked contract/section audits. Requirements and issue acceptance remain binding; detailed implementation choices may evolve with recorded evidence while preserving those contracts. If discovery reveals an actual architectural conflict or product tradeoff, present the concrete conflict and remedy rather than silently reducing scope.

**Decisions complete:** product scope, React/Python split, data/storage/owner boundaries, native acquisition, publication/retention architecture, local inference/search approach, Tunnel, MIT, full camera views and homepage placement.

**Execution discovery pending:** exact owner admission/interfaces, capacity and timing, NFS fencing, provider concurrency/durability/Barman compatibility, CPU/GPU/model qualification, independent recovery copy/bootstrap details and operational limits. These are assigned work, not additional planning research gates.

**Implementation pending:** all application code, owner provisioning/integration, qualified builds/deployment, full browser and failure tests, independent recovery proof and rollout. No execution workload or application deployment is claimed by this handoff.
