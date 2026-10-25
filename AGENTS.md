# weather

<!-- estate-contract:start — canonical copy lives in /Users/jason/AGENTS.md; keep this block identical in every repo -->
## Operating contract

Jason's direct request authorizes the work it describes, across code, infrastructure, and Jason-owned services in scope, through delivery and verification of the real end state. Ask only when an unresolved ambiguity could materially change the outcome. Add approval, review, issue/PR, or planning steps only when Jason asks for them or a branch rule enforces them; process descriptions in repository docs are context, not gates.

- Make the smallest complete change in the repository that owns the thing being changed (see Ownership). Fix forward; prefer rollback when it restores service faster or limits harm. Skip speculative abstractions and unrelated cleanup.
- Preserve unrelated work; use an isolated worktree when another checkout may be active. Resolve destructive targets exactly and keep practical recovery data.
- Validate in proportion to risk: the cheapest reliable check of the changed behavior (focused test, direct probe, or readback; docs-only edits need diff/readback). Add tests only for meaningful regression risk: critical behavior, security, data integrity, or subtle logic. Reuse existing CI/CD, honor enforced checks, and add pipelines or gates only for a concrete need in the task.
- Verify the requested end state from the authoritative source or live behavior; passing CI alone does not prove a deployment works. Report what changed, what was checked, and any remaining limitation.
- Search narrowly, batch independent lookups, reuse current evidence, and keep plans and updates brief.
- Use existing credentials without printing, committing, or logging secret values or Kubernetes Secret data; rotate or revoke credentials only when Jason asks.

### Execution location and resource use

- Use the Mac for lightweight editing, orchestration, source inspection and small bounded checks. Run CPU- or memory-intensive tests, experiments, simulations, replay, training, builds and long-running processes as containerized workloads in the authorized k3s environment, through the repository's supported CI/GitOps or job-submission path. Colima, Docker Desktop and other container VMs on the Mac still consume the Mac's resources and do not satisfy this requirement.
- Classify work by its expected workload, using existing repository and estate resource thresholds: sustained CPU saturation, substantial memory or dataset use, broad parallel suites, image compilation and prolonged processing require offloading. A command listed as "local" or "offline" describes a workflow, not permission to run intensive work on the Mac. If size is uncertain, inspect the workload and use a bounded pilot in the authorized cluster path before expanding it; do not benchmark the heavy workload on the Mac to decide.
- Before execution, confirm the authorized cluster/context, target namespace, supported submission path, dependencies and data access. Base placement on current k3s allocatable CPU/memory, existing resource requests/limits, pod working sets, scheduler and quota admission, node pressure and existing workload owners/admission reservations. Physical-host diagnostics are distinct from Kubernetes schedulable capacity: a Proxmox host MemAvailable alert alone does not prohibit k3s placement. Preserve existing fixed-allocation and control-plane safety rules, and healthy and recovery capacity. Use bounded concurrency, deadlines and stable job/operation IDs; retain outputs and logs in approved custody and verify terminal completion and output integrity before claiming success.
- Keep experiments isolated from production workloads and storage side effects unless Jason explicitly authorizes those effects. Use existing approved identities and access; do not create credentials, RBAC, security settings, privileged workloads or persistent access to enable an experiment. Keep secrets out of manifests and logs. Clean up only the job's disposable resources through the approved lifecycle; preserve retained data, outputs and shared PVCs.
- If the supported cluster path, required access, ownership/admission clearance or safe capacity is unavailable, stop the intensive work and report the exact blocker. Do not silently fall back to intensive execution on the Mac. Work that inherently requires local execution needs an explicit, task-scoped exception approved by Jason before it runs, with its workload bounds stated. This policy is the estate minimum: preserve stronger repository/subdirectory restrictions, existing holds and data-custody rules. A repository command or general task authorization does not override this placement policy; only Jason's explicit scoped exception does.
<!-- estate-contract:end -->

Owns the public Weather application at `weather.vallery.net`: Tempest telemetry history, three-camera civil-twilight timelapses and camera products, local almanac, AI descriptions, search, editor and the vallery.net weather card. Design: [docs/architecture.md](docs/architecture.md). Binding changes since requirements v4.0: [simplification record](docs/decisions/2026-10-24-simplification.md), amended by the [takeover re-plan](docs/decisions/2026-10-24-takeover-replan.md) (C1–C16), which wins where they conflict.

## Owns

Application lane. Per-item detail: [OWNERSHIP.md](OWNERSHIP.md). Central record: [ESTATE-INVENTORY.md](https://github.com/jvallery/agents/blob/main/docs/fleet-inventory/ESTATE-INVENTORY.md). Change the central entry and OWNERSHIP.md in the same change set.

- Namespace `weather` and everything `deploy/k8s` renders into it, including CNPG Cluster `weather-pg`, its PVCs and generated Secrets.
- `weather.vallery.net`, as both backend and route owner (IngressRoutes in `weather`).
- Images `registry.vallery.net/jvallery/weather-app` and `weather-web` and their digest pins, plus the deployed content of Argo Application `weather`.

## Adjacent lanes

Lane table and rules: [estate README](https://github.com/jvallery/agents/blob/main/docs/estate/README.md) 4.1 and 4.3. The Weather agent authors each change below in the owning repository and links it from the owner issue in the [simplification record](docs/decisions/2026-10-24-simplification.md#owner-prerequisites-after-the-re-plan).

- **agents:** fleet CI registry record `gh-1431367372` (`ci-only`; no coding repo pod), AppProject/Application, `secrets-ksops/weather`, Authentik, Multus NAD.
- **storage-infra:** Garage buckets and keys, the NFS staging PV, the B2 buckets and keys, central custody.
- **network-infra:** IPAM, hostname and tunnel records, `ak-forwardauth`.
- **monitoring-stack:** probes, alert rules and dashboards (R13).
- **sentinel:** camera access and credentials. The Tempest hub device is homeassistant-owned.
- **cortex-ai-compute:** inference routes and keys.
- **VerdifyConsultancy/wordpress:** the vallery.net homepage.

## In transition

- Claude took over from Codex on 2026-10-24. The [takeover re-plan](docs/decisions/2026-10-24-takeover-replan.md) records the audit, decisions C1–C17 and the critical path (C18 arrives with #123). Codex's shutdown handoff branch (`docs/weather-shutdown-20261024`) is history, not instructions. Claude paused on 2026-10-25; the resume handoff is the latest comment on #36.
- Live (2026-10-25): slice 1 (current conditions, history, almanac, events, embed card), the search UI and backend (lexical only until #124), the media origin and catalog, `weather-pg` (3 instances, Barman to B2, PITR drilled), the Tempest receiver and three collectors at 0.25 Hz. The camera pipelines and `weather-preview` run 0 replicas, so no timelapse, keogram, still or preview is published yet; #127 then #124 start them.
- Migration chain on main: 0001 → 0002 → 0003 → 0004 → 0005 → 0006 → 0009_stills → 0007_previews → 0010_cleanup → 0011_gc_kept → 0008_keograms → 0012_analysis (#23); #44's `0013_editorial` chains on `0012_analysis`.
- Fleet CI shares 16 `repo-validate` slots across the estate and a Weather PR needs up to 3 for about 25 minutes. Keep at most about 3 Weather PRs in CI at once and terminate superseded runs; nine at once queued for hours on 2026-10-25.
- agents#5366 delivered Weather's `ci-only` ownership, registry, generated CI profiles, GitOps roots and enforced branch protection. Weather has no coding repository pod. Planning history lives in [docs/archive/planning-2026-10/](docs/archive/planning-2026-10).

## Deliver and verify

- **Picking work.** An issue is ready when every native "blocked by" issue is closed. Take any ready issue; when blocked on an owner, take another ready issue instead of waiting. List the ready set with:

  ```sh
  gh issue list -R jvallery/weather --state open --json number,title --limit 100 | jq -r '.[]|"\(.number) \(.title)"'
  gh api repos/jvallery/weather/issues/N/dependencies/blocked_by --jq '.[]|"\(.state) \(.html_url)"'
  ```

- **Local checks.** Run `scripts/ci.sh` locally only for lint, typecheck and focused unit tests. Integration suites, Playwright, image builds and media or render pilots run in-cluster through fleet `ci_submission` / repo-build (Kaniko → zot). Never run them on the Mac.
- **Promotion.** A `deploy:` commit pins image digests in `deploy/k8s`, and Argo auto-syncs Application `weather`. A change is deployed when Argo shows Synced + Healthy at the intended revision and the pinned digest is running.
- **Definition of done** (every issue):
  1. Merged to `main`.
  2. Checks green in fleet CI. The scaffold, time-model and collector issues (#3, #10, #11) may close before fleet CI exists (WX-01, #2); they close on lightweight `scripts/ci.sh` checks, and WX-01's first fleet run re-verifies them.
  3. Deployed and verified live, for deployable work.
  4. New failure modes expose metrics, and the alert/probe PR to monitoring-stack is merged where the issue says so.
  5. A runbook section is added under `docs/operations/` for anything operable.
  6. OWNERSHIP.md is updated for new objects.
  7. No secret, exact coordinates, camera IP or station serial in Git, issues or logs.
  8. The issue gets a closing comment: what changed, what was verified (command plus result), and remaining limits.
- **Proof and comments (C2).** Don't commit evidence, receipt, custody or handoff files. CI proof is the PR's fleet check link; live proof goes in the closing comment. Each issue gets at most one blocker note, naming the decision or owner, and one closing comment. Don't post progress checkpoints, workflow or pod UIDs, or local paths.
- **No self-imposed gates (C1).** Acceptance is the issue's checklist. Don't add qualification programs, held-tab or placement-reservation proofs, or zero-failure clocks. A probe window means availability ≥99.5% over the window, measured after the relevant digests are live.
- **Issue bodies.** Outcome; Acceptance (at most about 10 checkboxes, each one a command or an observable behaviour); Blocked by (native links only); Not in scope. Refer to issues by number; WX keys are retired.
- **Contracts.** API contracts are code-first (Pydantic → OpenAPI → `openapi-typescript`). A vector file in `docs/contracts/` exists only if a test loads it.
- **Disposable namespaces.** Pilots and qualification namespaces carry `estate.vallery.net/disposable=true` and `estate.vallery.net/expires=<date>` (estate R23). Delete them once the result is recorded in the issue.
- **Restore drills.** Restore drills (#6 and #33) are the exception: they run as `weather-drill-*` resources inside `weather` with the disposable/expires labels, so read-only restore keys are referenced in place and never copied. A drill NetworkPolicy isolates them from production, and they are deleted after use.

## Hazards

- **Never delete the database.** The CNPG Cluster, PVCs and namespace carry `Prune=false,Delete=false`. Never delete them to redeploy. Deleting a data PVC, PV, bucket or database needs a snapshot and Jason's explicit instruction.
- **Privacy.** Camera originals are private. Public images must satisfy the ≥600 s embargo at request time. Never publish exact coordinates, camera addresses, station serials or credentials.
- **Cameras.** Collectors may only poll their own camera's snapshot endpoint at the commissioned cadence. Public requests never trigger camera access.
- **Disabled-by-default manifests.** Ship a new workload's manifests disabled (`replicas: 0`, or left out of the kustomization) until its Secrets, PVCs and keys exist. The issue that commissions it, or the slice launch, enables it. Argo `weather` must never go Degraded for want of a prerequisite.
- **Migrations.** Never rewrite a migration that has run on `weather-pg`. Before the first production migration (#74), squash freely; after it, use expand/contract only. The migrate Job is an Argo Sync hook at wave 1, and DB clients run at wave ≥2.
- **Generated CI config.** `.agent-fleet/ci.yaml` is generated from the agents registry. Change the registry, not the file.
- **Inference.** The Cortex routes are shared with other consumers. Keep AI concurrency at 1 and stay within the scoped key's limits.
