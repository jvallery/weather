# D01 — Independent repository and delivery trust boundaries

Status: repository/license and delivery architecture selected; fleet enrollment, private overlay location and isolated untrusted execution remain material OWN-CI decisions. Requirements §§4.1–4.3, 16.1–16.3 and 18.2; consumers WX-01/02/34. No workflow, registry enrollment, namespace, repo cell or deployment is created by this ADR.

## Confirmed baseline

Weather is an independent public repository on main. Jason selected [MIT with compatible permissive reuse](license-and-reuse.md); preserve upstream license/provenance obligations and separately review copyleft imports. No upstream application is adopted wholesale. Public source and synthetic fixtures must remain reusable without household credentials, private topology or exact location.

E09 (2026-10-23) reads current GitHub and cluster metadata: secret scanning and push protection are enabled; Weather has zero GitHub workflows and zero repository rulesets. The current registry-driven validation Sensor has no Weather dependency. Its four PR dependencies require the head/base repository names to match. The native CI controller has one Ready replica. These observations establish existing surfaces and absent Weather enrollment, not a successful Weather run or a negative isolation test.

The inspected event-path renderer admits default-branch pushes and same-repository PRs from registry-owned profiles, with exact head identities and constrained submission. It does not establish a foreign-fork path. Older runner-policy text describes VM/ARC labels and future transitions; reconcile that with current generated admission and live controllers instead of copying historical labels. Existing unrelated legacy Sensors are not a reason to create another per-Weather event route. Exact source/live metadata is retained privately in OWN-CI.

## Repository, ownership and configuration

Weather owns application code, schemas, synthetic fixtures, public generic deployment templates and its namespace application objects. Existing owner lanes retain shared providers, grants, DNS/tunnel, monitoring definitions, CI admission, images/registry infrastructure and credential custody. Register ownership and update the responsible OWNERSHIP records when resources are implemented. The private Weather deployment overlay belongs in the existing owner-managed delivery system; OWN-CI must select its exact path, AppProject/catalog entry and central KSOPS integration before namespace implementation.

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

D01 remains open for concrete registry/overlay/AppProject selection, supported untrusted admission and actual check/provenance/delivery qualification. MIT selection and public repository settings are complete; pipeline existence and production readiness are not.
