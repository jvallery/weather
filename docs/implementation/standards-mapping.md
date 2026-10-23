# Estate standards applicability and evidence

This is a planning mapping, not a compliance certificate. The [machine-readable matrix](standards-mapping.json) covers all 46 standard IDs in the pinned private [estate baseline](https://github.com/jvallery/agents/blob/9bbc5f1e4a6349c05dde311234da22aa82d67f0b/docs/estate/STANDARDS.md), read on 2026-10-23. Only sanitized obligations are published here. All rows remain unverified for Weather because it has no deployed namespace/workloads yet.

Direct obligations belong to Weather. Shared obligations require a scoped owner integration and readback. Inherited obligations remain with the platform owner; a missing platform proof is disclosed and its effect evaluated, not silently treated as compliant or expanded into an unrelated Weather migration. [Owner prerequisites](owner-prerequisites.md) provide actual issue links.

| Standard | Applicability | Weather acceptance owners | Proof required |
|---|---|---|---|
| STD-HA-01 | shared | WX-05, WX-32, WX-35 | Prove admitted DB/API placement across physical host failure domains and isolated single-host loss behavior; record NAS capture/media outage dependency and service tier. |
| STD-HA-02 | shared | WX-03, WX-05, WX-06, WX-32 | Map actual durable copies and independent failure domains; no unsupported node pinning. RF1 archive blocks and NFS staging do not become HA because database replicas exist. |
| STD-HA-03 | inherited | WX-01, WX-32, WX-34 | Record dependency recovery for GitOps, DNS and monitoring; consume owner proof or report its absence. Weather does not redeploy shared controllers. |
| STD-HA-04 | direct | WX-01, WX-05, WX-33 | Validate single-replica drain policy and operator-supported database PDB behavior; capture writer replacement still requires fencing. |
| STD-HA-05 | inherited | WX-03, WX-06, WX-32, WX-34 | Reference owner power-order/flush evidence; document outage/restart dependencies. No live household power drill in this planning pass. |
| STD-DR-01 | shared | WX-29, WX-30, WX-32 | Enumerate every retained data class, three independent copies, two media, offsite immutable/offline custody and full verification. Separate ephemeral dense capture/preview exceptions from durable history. |
| STD-DR-02 | shared | WX-07, WX-28, WX-29, WX-32 | Read back actual offsite key capabilities, scope and Object Lock/retention; test denied destructive actions through approved qualification. Never distribute management keys to application pods. |
| STD-DR-03 | direct | WX-28, WX-29, WX-31 | Weather-owned Barman plugin base/WAL objects, fresh successful base and contiguous recoverable WAL; logical exports alone are insufficient. |
| STD-DR-04 | shared | WX-30, WX-32, WX-34 | Scheduled isolated semantic restore with achieved RPO/RTO, checkpoint closure/hash proof and stale-drill alerts. Recovery runs without original storage or inference. |
| STD-DR-05 | shared | WX-29, WX-32, WX-34 | Resolve recovery configuration, identities and decryption keys without original Mac/NAS/DB; verify independent escrow and cold readback, not merely a file on this Mac. |
| STD-OBS-01 | direct | WX-31, WX-33 | Delivered rules cover pods/jobs/replicas, PVC/node pressure, clock/freshness and missing series; rule tests plus live nonempty evaluation. |
| STD-OBS-02 | direct | WX-29, WX-31, WX-32 | Separate base/WAL/media/joint ages and failures, missing receipts and expired silences; each producer maps to owner/runbook. |
| STD-OBS-03 | direct | WX-13, WX-19, WX-25, WX-31 | Define denominators, error budgets and multiwindow request SLO burn; separately measure completed capture/publication work and stale data. |
| STD-OBS-04 | shared | WX-19, WX-31, WX-35 | Probe the actual public origin and representative eligible media; distinguish route, dependency and data freshness failures. |
| STD-OBS-05 | shared | WX-31, WX-34 | Resolvable runbooks and impact-based severity; verify existing receiver routing through owner workflow. No outbound product notification feature is added. |
| STD-OBS-06 | shared | WX-01, WX-31, WX-34 | Verify sanitized Weather logs reach retained central logging with bounded volume; no raw packet, credential, private note or model-response logging by default. |
| STD-K8S-01 | shared | WX-01, WX-33 | Restricted namespace/default seccomp and workload security checks; audit logging is inherited owner evidence, not a new Weather cluster-admin permission. |
| STD-K8S-02 | direct | WX-01, WX-07, WX-33 | Least-privilege roles and no automatic API token where unnecessary; negative can-i/access tests, no broad Secret listing or long-lived service-account tokens. |
| STD-K8S-03 | shared | WX-01, WX-04, WX-15, WX-33 | Nonroot/unprivileged pods; any UDP/GPU host attachment requires exact supported owner exception and isolation qualification, not blanket hostNetwork. |
| STD-K8S-04 | direct | WX-01, WX-04, WX-10, WX-33 | Default-deny ingress and explicit bounded egress; collector can contact only assigned camera. Node-managed NFS is separately documented and tested. |
| STD-K8S-05 | direct | WX-02, WX-05, WX-35 | All running containers/init containers use admitted local registry digests; verify actual image IDs, mirror coverage and offline core startup. |
| STD-K8S-06 | inherited | WX-01, WX-34 | Reference owning platform's at-rest key cadence and recovery evidence; do not rotate or revoke credentials as an incidental Weather action. |
| STD-EXP-01 | direct | WX-19, WX-20, WX-25, WX-33, WX-43, WX-44 | Record exact public read-only route disposition including read-only search POST; private operator/diagnostic routes require authenticated authorized access with MFA-backed identity and negative tests. |
| STD-EXP-02 | shared | WX-07, WX-19, WX-33 | No public provider/catalog/archive/raw/diagnostic bypass; bound request sizes/rates and sanitize errors. Public requests never trigger camera capture. |
| STD-EXP-03 | inherited | WX-01, WX-04, WX-33 | Weather workload egress cannot reach unrelated management planes; inherited management MFA/SSH remediation stays with platform owners. |
| STD-EXP-04 | shared | WX-02, WX-33, WX-34 | Verify repository secret scanning/push protection/vulnerability alerts and least-privilege app installation; review dependency updates without enabling untrusted access to fleet credentials. |
| STD-EXP-05 | shared | WX-19, WX-33, WX-44 | Actual origin TLS/HSTS and response-header readback; narrow framing exception only on embed route, with private routes denied framing. |
| STD-CI-01 | shared | WX-02, WX-35 | Before deploying a branch, verify PR/required automatically triggered checks and no standing bypass against the fleet baseline; do not invent a required status with no producer. |
| STD-CI-02 | shared | WX-02 | One check entrypoint on supported registry-driven fleet submission; reconcile baseline ARC wording with current native event path; foreign-fork exclusion is not safe untrusted CI. |
| STD-CI-03 | direct | WX-02, WX-33 | Observe exact-SHA main checks, success/cancellation history and under-15-minute standard target; isolate slower qualified experiments from normal validation without skipping critical tests. |
| STD-CI-04 | shared | WX-02, WX-33, WX-35 | Bind image SBOM/signature/provenance and full-SHA action pins to running digest; verify admission policy and failure on invalid provenance, not merely attach a build artifact. |
| STD-CI-05 | inherited | WX-31, WX-34 | Reference scheduled platform conformance and stale-run signal; add Weather-specific scheduled drift/restore obligations through owners rather than duplicate cluster-wide jobs. |
| STD-IAC-01 | direct | WX-01, WX-02, WX-35 | Desired state and named controller ownership come from Git; verify managed fields, with migrations/jobs using supported scoped operations rather than manual Argo overrides. |
| STD-IAC-02 | direct | WX-35 | Weather application at intended revision is Synced/Healthy with correct digest and no unexplained prune/orphan state; then verify product behavior. |
| STD-IAC-03 | shared | WX-01, WX-35 | Weather Application/AppProject is parented by existing agents roots; declaration, tracking identity and namespace boundaries match. |
| STD-IAC-04 | shared | WX-03, WX-04, WX-06, WX-19, WX-34 | Owner Git declares NAS/network/DNS grants; apply via supported idempotent paths and verify drift, not hand-created unmanaged infrastructure. |
| STD-OPS-01 | shared | WX-02, WX-05, WX-14, WX-34 | Version support and vulnerability/patch policy cover shipped runtime, media codecs and DB/extensions; shared host/kernel work remains owner-managed. |
| STD-OPS-02 | direct | WX-02, WX-34 | Version register/update detection records exact image/library/codec/extension/schema/model-profile identities and compatibility/qualification status. |
| STD-OPS-03 | shared | WX-01, WX-19, WX-31, WX-34 | Track certificate/domain/identity deadlines with adequate warning and owned renewal; expired identity must not trigger anonymous fallback. |
| STD-OPS-04 | inherited | WX-31, WX-34 | Reference platform etcd maintenance and capacity evidence; Weather cannot administer etcd. |
| STD-OPS-05 | shared | WX-03, WX-05, WX-06, WX-14, WX-15, WX-29, WX-31 | Admit actual scheduler/storage/N-1 headroom, runway and bounded write growth; normal below-80% target is distinct from emergency stop watermarks. No intensive Mac fallback. |
| STD-ORG-01 | direct | WX-01, WX-02, WX-14, WX-15, WX-31 | Every container/init/experiment declares bounded CPU/memory requests and memory limit, concurrency and deadline; prove workload fit against live admission. |
| STD-ORG-02 | shared | WX-01 | Weather namespace ownership label, quota and LimitRange come from central ownership/admission contract and match readback. |
| STD-ORG-03 | direct | WX-02, WX-18, WX-30, WX-34 | Disposable job/replica/CI state expires through supported lifecycle; retained logs/receipts/data and recovery references are preserved, not swept with job TTL. |
| STD-ARCH-01 | shared | WX-01, WX-03, WX-04, WX-06, WX-19, WX-31, WX-34, WX-44 | One current owner per resource, one secret lane, central record plus owner inventory, scoped routes/certs and actual monitoring renderer; reconcile archived site owner before embed. |
| STD-ARCH-02 | shared | WX-02, WX-05, WX-28, WX-31 | Reuse supported Barman plugin, registry mirrors and CNPG metrics; verify real installed compatibility rather than copying legacy syntax. |

## Material gaps and decisions this mapping exposes

**Copies and immutability (DR-01/02).** A single Garage origin plus one central B2 destination is two named copies, not proof of three copies/two media/immutable or offline recovery. Database replication does not supply an additional backup copy of every media original. Multiple versions in one provider bucket do not establish an independent third failure domain. OWN-CUSTODY must select and qualify a complete retained-data custody disposition within the approved central ownership model, or identify an exact owner decision still needed. One canonical application bucket remains fixed; this does not authorize another application store or a separate Weather B2 uploader. Existing backup jobs being active proves neither Weather enrollment nor Object Lock settings. Do not mark D15 complete from the current topology.

**Recovery bootstrap (DR-05).** The current raw research receipt exists on the operator surface. That is not independent escrow. Weather recovery must resolve exact config/images/credentials and required decryption keys without relying on that Mac or the failed NAS/database. Existing central escrow evidence must be inspected before duplicating any secret custody.

**E02 follow-up:** [Live provider metadata](../research/custody-evidence.md) now confirms the central private destination's 45-day governance default and scoped no-version-delete/no-bypass writer, with a separate read-only identity. This resolves policy discovery, not Weather enrollment or per-object/full-closure protection. Database primary/local archive/offsite can supply three named copies after qualification; retained media copy coverage remains open. STD-DR-01 does not demand three physical sites: qualify usable retained copies and required media separation rather than infer compliance or failure solely from provider version counts. The private owner issue now retains the E02 metadata receipt independently of the Mac file; decryption-key cold bootstrap remains unproven.

**Public route exception (EXP-01).** The accepted product explicitly publishes sanitized observational weather, delayed media and bounded search. Record its exact public read-only routes from the OpenAPI contract, including read-only POST search. The baseline's 2026-10-21 clarification permits a documented owner-approved publishing exception; it does not exempt editorial writes, private originals, raw diagnostics, database archives or private notes. Operator routes require the approved MFA-backed identity plus server-side authorization. Route-method allowlists and negative tests must survive proxy aliases and alternate paths.

**Current CI versus baseline wording (CI-02).** The pinned agents event-path contract uses registry-generated admission and existing native validation/build templates. It admits default-branch pushes and same-repository PR heads; foreign forks are excluded. The baseline names fleet ARC runners. OWN-CI must document the supported current equivalent and preserve the single-entrypoint, immutable identity, scoped credential and local execution requirements. Do not create a separate runner/Sensor or treat excluded fork execution as implemented untrusted-contribution support.

**Branch policy (CI-01).** Current Weather is a planning repository with no deployed branch or pipeline. Ruleset readback is empty. Before application deployment, WX-02 must install and read back the actual required checks through the owner workflow. No nonexistent check is required merely to give an appearance of protection; passing a manually selected workflow is not automatic PR gating.

**Capacity (OPS-05).** The normal headroom standard and capture emergency stop thresholds serve different purposes. A proposed stop at 90% does not authorize normal operation above the baseline's 80% storage target. Admission must include actual NAS free space, separate export quota, retained promotions, render scratch, dense write throughput, custody runway and other owners. Current source inventory/working-set samples are discovery only.

**HA and NAS (HA-01/02/05).** The required capture staging and archive tier have documented NAS dependencies. Three database instances cannot remove them. D03/D06/D07 must show exact failure domains and accepted degraded behavior; classify service tier and any exception explicitly rather than call the entire product host/NAS-fault tolerant.

## Verification ladder

1. Source: pin the owner contract and proposed resource/route/identity; record allowed exception or missing decision.
2. Build: exact-SHA required checks, image digest, provenance and admission identity. This does not prove deployment.
3. Deploy: parented Argo revision/digest plus live resource/role/policy/config readback. This does not prove product behavior.
4. Product: fresh measurements, captured inputs, valid publication, authorized origin playback and explicit degraded states under bounded failure cases.
5. Recovery: independent usable base/WAL and object closure, escrow availability, isolated semantic restore and measured age/duration.

Each acceptance receipt identifies its layer, timestamp, target, exact source/image/config, method, result and remaining uncertainty. Store raw sensitive evidence privately. Readiness requires the relevant evidence to remain current; a closed owner issue is not permanent resource admission. The matrix does not add a new process gate or authorize broad estate remediation.

## Current bounded observations

On 2026-10-23, the cluster query found no weather namespace. GitHub reports the public repository active, secret scanning and push protection enabled, and no rulesets. Automatic Dependabot security updates are disabled; this setting is distinct from vulnerability-alert enablement. These observations are repository/planning facts only. No application security, durability, availability or operational standard is marked satisfied.

