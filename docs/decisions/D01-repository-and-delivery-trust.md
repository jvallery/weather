# D01 — Independent repository and delivery trust boundaries

Status: repository/license and delivery architecture selected; private placement is selected under E12; fleet enrollment and isolated untrusted execution still require OWN-CI implementation/admission. Requirements §§4.1–4.3, 16.1–16.3 and 18.2; consumers WX-01/02/34. No workflow, registry enrollment, namespace, repo cell or deployment is created by this ADR.

## Confirmed baseline

Weather is an independent public repository on main. Jason selected [MIT with compatible permissive reuse](license-and-reuse.md); preserve upstream license/provenance obligations and separately review copyleft imports. No upstream application is adopted wholesale. Public source and synthetic fixtures must remain reusable without household credentials, private topology or exact location.

E09 (2026-10-23) reads current GitHub and cluster metadata: secret scanning and push protection are enabled; Weather has zero GitHub workflows and zero repository rulesets. The current registry-driven validation Sensor has no Weather dependency. Its four PR dependencies require the head/base repository names to match. The native CI controller has one Ready replica. These observations establish existing surfaces and absent Weather enrollment, not a successful Weather run or a negative isolation test.

The inspected event-path renderer admits default-branch pushes and same-repository PRs from registry-owned profiles, with exact head identities and constrained submission. It does not establish a foreign-fork path. Older runner-policy text describes VM/ARC labels and future transitions; reconcile that with current generated admission and live controllers instead of copying historical labels. Existing unrelated legacy Sensors are not a reason to create another per-Weather event route. Exact source/live metadata is retained privately in OWN-CI.

## Repository, ownership and configuration

Weather owns application code, schemas, synthetic fixtures, public generic deployment templates and its namespace application objects. Existing owner lanes retain shared providers, grants, DNS/tunnel, monitoring definitions, CI admission, images/registry infrastructure and credential custody. Register ownership and update the responsible OWNERSHIP records when resources are implemented. The private Weather deployment overlay uses the existing owner-managed Application catalog and central KSOPS lane selected below under E12. OWN-CI implements and verifies those exact coordinates; Weather keeps the reusable application and bootstrap bases.

Keep the namespace, service accounts, RBAC, quotas, secrets, image names and CI identity independent. Do not inherit Sentinel's privileges or make an interactive repo cell a prerequisite for building Weather. Public examples use unmistakable nonproduction placeholders and fail without explicit deployment configuration. Exact camera URLs/location, source receipts and provider credentials remain outside this public repository.

## Trust-separated execution

| Execution class | Inputs and authority | Required boundary |
|---|---|---|
| Untrusted contribution validation | Immutable PR/head source plus synthetic JPEG/UDP/model/object fixtures | Disposable owner-admitted k3s execution, no estate credentials or production network access, no writable publish registry or deploy authority. |
| Trusted build | Exact admitted source/profile and pinned dependency inputs | Supported fleet build path; scoped registry output and build provenance; no camera or production data access simply to compile an image. |
| Trusted integration | Exact candidate digest plus explicitly admitted fixture/device scope | Separate identity/resources, bounded live tests and private evidence; no implicit expansion from unit-test rights. |
| Promotion/deployment | Verified immutable image/provenance, compatible schema/config and approved target revision | Existing owner GitOps/catalog path with exact target, required checks, rollout observations and retained rollback coordinates. |

Foreign-fork exclusion protects the current trusted path but does not satisfy the requirement to validate public contributions safely. Select an admitted disposable untrusted k3s path; ordinary job isolation, a self-hosted label or branch protection alone is insufficient. OWN-CI must prove the network, credentials, filesystem, cache and artifact boundaries. No privileged `pull_request_target` checkout, token projection, inherited kubeconfig, host socket/mount, service-account API token or access to private images/artifacts is acceptable for fork code.

Treat build scripts, package hooks, tests, Dockerfiles and PR-edited workflow/configuration as executable untrusted input. Fetch immutable source/dependencies through owner-approved mechanisms that do not expose fetch credentials to the executed checkout. Untrusted artifacts/cache keys cannot overwrite trusted build inputs or become a promoted image merely because a reported check passed. Private logs must not be uploaded publicly. A same-repository head is an admission fact, not universal proof that arbitrary new privileges in that checkout are safe.

The current supported isolated path is not yet demonstrated. Do not make a new Sensor, credential, RBAC policy, privileged runner or Mac container environment to bypass this planning gap. Heavy execution stays on the authorized admitted cluster path. A synthetic pilot follows supported submission, capacity/admission checks and bounded resources; this ADR authorizes no experiment by itself.

## Checks, builds and promotion

Use one project-owned check entrypoint exercised consistently by the supported fleet validation contract. Pin source SHA, dependencies, toolchain/FFmpeg build configuration and container inputs; keep synthetic validation independent of private deployment settings. Cover schema/semantic contracts, meaningful migrations and negative privacy/data-integrity behavior. Required-check names must bind exact repository/head and the actual generated execution contract; a check on an older head, an unrelated successful workflow or a forged artifact is insufficient.

The implementation must configure enforced required checks before deploying the protected branch; no ruleset is claimed installed now. Preserve Jason's direct release authority and existing enforced branch rules. Do not add an unrelated manual approval ceremony: the needed separation is technical authority and evidence, not an invented process gate.

Build application/FFmpeg images through existing in-cluster fleet builders into the approved registry, pin runtime digests and verify expected supply-chain signatures/SBOM/provenance under the estate standards. Scope registry write rights to the declared output. Runtime pulls must not silently fall back to public registries. Runtime packages/fonts/scripts/models are supplied locally and pinned; core operation cannot depend on public package installation or external weather/model services.

Release metadata binds application image, schema/migration compatibility, FFmpeg recipe, prompt/profile and private configuration revisions without secret values. Expand/contract migration steps preserve rollback and all retained observations. A trusted build passing CI does not prove deployment: verify exact Argo revision with Synced/Healthy status, expected running digest and actual product behavior. Database/media/custody recovery remains a separate proof obligation.

## Acceptance and rollback

WX-01 verifies the owner registry, namespace/RBAC/quota/secret boundaries and denied cross-role access. WX-02 proves exact-head automatic checks, safe untrusted synthetic execution, denied production reachability, trusted digest/provenance and mutation-resistant artifact promotion. Include fork event, repository-ID spoof, stale head/base, replayed event, malicious package hook, cache poisoning and a PR editing its own CI definition. Reconcile event deduplication and stale-result handling against the actual owner controller rather than implementing a parallel system.

WX-34 proves staged deployment, source-to-running-digest linkage, compatibility and rollback with retained data. Capture pre-state and retain the last verified digest/configuration; rollback application/config without destructive down-migration or deleting recovery evidence. An admission or signature failure stops promotion and reports the exact failure; it does not authorize bypassing checks or using an alternate public image.

D01 remains open for enrollment of the selected registry/overlay/AppProject coordinates, supported untrusted admission, compatible build pins and actual check/provenance/delivery qualification. MIT selection and public repository settings are complete; pipeline existence and production readiness are not.

## E11 — concrete untrusted-path disposition

A subsequent read-only owner-source and live-policy review narrows the earlier E09 gap. The registry event path admits default-branch and same-repository PR validation; foreign-fork heads remain excluded. The existing candidate executor is a separate source-capsule architecture with exact head/base/merge binding, but its identity validator is deliberately restricted to the owner's repository. Weather cannot submit under a substituted repository identity. Current candidate-network policy includes application-specific proxy exceptions, so the live namespace is not automatically a clean Weather sandbox. Readiness of two proxy replicas establishes availability only, not hostile-source isolation.

Select an **owner-managed extension of the existing candidate execution contract**, not foreign-fork execution through the current trusted event lane. OWN-CI must implement explicit Weather repository/immutable-source admission and a separate restricted candidate policy boundary, using the existing orchestration and disposable workload lifecycle. Do not widen the shared existing namespace's policy or copy its exceptions. This is a selected implementation approach, not a currently supported Weather submission command or authority to create that boundary during planning.

The extension contract must:

- Bind actual repository identity, PR/head/base/merge source, operation/run/attempt and supported component before source acquisition and again before execution. Preserve denial for every unenrolled identity; never spoof the owner's identity to pass the existing validator.
- Have trusted orchestration obtain public source and immutable synthetic inputs, then supply a credential-free read-only source capsule to the candidate. Candidate code cannot select the trusted capsule/controller, validation policy, image digest, network policy or promotion authority.
- Use disposable candidate storage and explicit CPU/memory/disk/deadline admission; no host mounts/devices, Kubernetes token, production PVC, camera/database/model credential or writable trusted cache. Nested build privileges, if needed by the existing runner, require the owner's exact reviewed profile; do not infer security from a rootless label.
- Give candidates only the qualified dependency access necessary for synthetic checks, with no inherited application ingress exceptions. Any registry dependency access is read-only and scoped to approved build inputs, not general storage/API access or image publication. Validate proxy bypass, redirects, literal IPs, DNS/rebinding and alternate address families as applicable to the actual network stack.
- Keep synthetic PostgreSQL/object fixtures within the disposable boundary. Permit result extraction only after bounded termination and inventory/hash checks; artifacts and logs are untrusted and cannot instruct promotion or expose trusted credentials.
- Report checks for the exact source through the trusted controller. A passing untrusted run does not publish an image or authorize deployment; trusted build/integration/promotion retain their separate contracts.

OWN-CI's private E11 update records exact source revision/files, runtime policy observations and the local metadata-only receipt. No Secret was read, no candidate Job executed and no namespace/policy/identity changed. Required implementation qualification covers foreign-fork admission, changed CI/package hooks, wrong identity/source, network/credential denial, timeout cleanup and result tampering. P1 still needs exact owner integration/enrollment and compatible pins; this source review supplies the concrete extension target and rejects unsafe use of the existing path.

## E12 — selected deployment placement and ownership

A read-only refresh of the fleet owner's current source confirms an existing pattern of private `Application.spec.source.kustomize` image overrides and targeted patches over a pinned application base. Select that pattern for Weather rather than a second private application repository or a new deployment controller. The source review also identifies CI-only registry records that do not create coding pods. Live metadata confirms both intended parent catalogs are SHA-pinned, Synced and Healthy at their respective declared revisions; this is evidence of the existing delivery surfaces, not Weather admission. The selected Weather namespace and Application names are absent at observation time. Exact source/file hashes, live parent pins and render images are retained in private E12/OWN-CI.

| Artifact | Selected source and responsibility |
|---|---|
| Reusable bootstrap base | Weather `deploy/k8s/bootstrap`: sole renderer of its Namespace and initial policy/quota/role objects; no workload or private secret values. Namespace is not recreated by a second Application. |
| Reusable application base | Weather `deploy/k8s/application`: application workloads, services, app-owned database/PVC declarations and namespace-scoped route contracts. No private endpoints/location/credentials or working production defaults in public templates. |
| Private environment overlay | Fleet owner's existing production Application catalog, using targeted `source.kustomize.patches` and digest-only `images` overrides. Exact declaration paths and names are recorded in OWN-CI. This is the one operative private overlay, not another copy of application YAML. |
| Catalog and AppProjects | Fleet owner declares three Applications: bootstrap, secret delivery and application runtime; separate narrowly scoped projects. Each declaration has one parent. No Weather nested root or hand-applied duplicate Application. |
| Secret custody | Existing central KSOPS lane for Weather's namespace; application bases reference delivered Secret names but do not render human/provider secret values. Storage-minted provider keys retain their registered owner exception and exact projections. No age key enters Weather. |
| CI/image ownership | Immutable GitHub repository ID maps to one CI-only registry record, with one producer for each declared image. Generated CI configuration comes from that record. Trusted builds publish scoped estate-registry images; the private catalog consumes immutable digests. No coding pod is a prerequisite. |

Use the exact private coordinate table in OWN-CI as the implementation target, subject to collision/ownership revalidation at execution. It names the registry record, bootstrap/runtime public paths, catalog declarations, projects, KSOPS directory and three image profiles (web, API and media worker). These are selected names, not provisioned resources, reservations or granted permissions. Public source keeps portable image placeholders and source contracts; private registry coordinates remain in owner custody.

### Namespace bootstrap and constrained authority

All three Applications use `CreateNamespace=false`. Bootstrap alone explicitly renders the Weather Namespace, with `Prune=false,Delete=false`, and the initial default-deny, role and quota policy. Its project permits the needed Namespace kind but no cluster RBAC, CRD or shared-operator mutation. AppProject kind permission is not a per-name constraint on cluster-scoped Namespace objects: trusted render/admission checks must independently enforce exactly the selected Weather Namespace and allowed object inventory. Do not copy a broad existing project's grants or assume destination namespace restricts cluster-scoped objects.

The secret project permits its exact namespace and required secret-render output only, not Namespace creation or workloads. The runtime project permits Weather's exact namespace and the resource kinds its declared base actually needs, not Secret-value sources or cluster resources. Registered controller-generated Secrets and exact provider projections remain explicit exceptions. An application dependency or resource need does not expand a project automatically; verify rendered object group/kind/name/namespace and owner against the admitted inventory.

Bootstrap must not deliver camera collectors, UDP listeners, inference calls or public routes. Defaults keep work disabled until private policy references and grants resolve. Namespace or secret availability alone cannot start unsafe work. Public bootstrap and application bases are pinned to reviewed Weather main-reachable revisions, and private patches are pinned to a reviewed owner revision. Arbitrary PR source and candidate-produced patches never enter trusted rendering or promotion.

### Delivery order and recovery coordinates

The implementation change set registers ownership and updates the responsible OWNERSHIP records, creates the CI-only record/generated outputs and declares projects/catalog/KSOPS sources. Do not sync a root merely because this design now names paths. Owner branch checks and current holds apply. Review complete parent diffs before advancing their pins: both selected parents can self-heal the full delta, including unrelated content between revisions. Never blanket-sync a held root or silently change its automation.

After the scoped parent declarations are safely adopted, sync only the exact Weather bootstrap revision and verify the Namespace, default-deny and admitted limits. Then deliver the exact secret-source revision and verify metadata-only custody/projection identity without exposing values. Only then sync the exact application revision and its trusted image digests with the corresponding config/policy hashes. Initial Weather child Applications are manual with pruning disabled; staged source-to-live acceptance in WX-34 determines later automation explicitly. No repeated user approval ceremony is added: the gate is existing authority, exact targets and validated evidence.

Keep bootstrap, secrets and runtime pins separate in release/recovery custody along with rendered inventory/hash, image provenance/digests and policy references. Mixed revisions must fail startup validation rather than run on guessed defaults. Rollback uses a compatible prior runtime/config revision while preserving Namespace, PVCs, database, archives and secret custody; deleting an Application or namespace is not rollback. WX-34 proves a clean bootstrap, version mismatch denial, failed-secret delivery, exact rendered scope, source-to-running-digest linkage and retained-data rollback. This source-backed placement closes the previously unspecified location decision, not the remaining resource grants, CI candidate extension, build pins or runtime qualification.
