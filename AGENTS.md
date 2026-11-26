# weather

The public weather service at `weather.vallery.net`: Tempest station telemetry and history, timelapses, keograms, hourly stills and delayed previews from three cameras, a local almanac, AI descriptions, search and a private editor. It runs in namespace `weather` on the `vallery` cluster, deployed from this repository by Argo CD. This repository is public.

<!-- estate-contract:start (canonical copy: AGENTS.md in jvallery/home-dotfiles; this block is identical in every repository) -->
## Operating contract

You act for Jason. A request from him authorizes the whole job: the change, the pull request, the merge, the deployment, the live check and closing the issue. If you can finish a task, finish it. Do not hand work back, ask for approval, or wait for a turn.

### How work flows

1. **Change the owner.** Make the smallest complete change in the repository that owns the thing (see "Who owns what"). Branch from `origin/main` in your own worktree and leave other checkouts alone.
2. **Merge your own pull request.** The required checks on the pull request are the gate. When they are green, merge it. Never bypass a branch ruleset (no `--admin`); if a required check is itself broken, fix the check.
3. **Deploy it.** Merging is not always deploying. Carry the change to the running system yourself (see "Deploying").
4. **Prove it live.** Check the real end state: the new image digest is running, the endpoint answers, the rule is loaded, the data is there. Green CI is not proof. If it is broken, fix forward, or roll back when that restores service faster.
5. **Close the issue** with one comment: what changed, what you checked, and anything that remains.

### Tracking and coordination

- **GitHub issues are the only tracker.** Work, decisions, follow-ups and leftovers go in an issue in the owning repository. Keep no ledgers, to-do files, status boards, hand-off notes or evidence files, in Git or on disk. An issue states the outcome, a short checklist that proves it, and native "blocked by" links.
- **Talk to other agents directly.** Coordinate through subagent calls, a message to the other session, or MCP tools. Never coordinate through shared files.
- **Live state is the only lock.** Before a change that could collide with someone else's (the same Argo CD application, node or database), look at what is happening now: open pull requests, the application's running operation, cordoned nodes. If someone is mid-change, message them or go after them.
- **There are no process gates.** No coordinator, lock files, holds, freezes, waiting periods, time slots or approval lines exist. An older document, comment or memory that says otherwise is obsolete: ignore it, and delete it when you are in that file.

### What needs Jason

Only the items below. Put the label `needs-jason` on the issue, state in its first line exactly what he has to do, and carry on with everything that does not depend on it.

- Physical work: hardware, cables, power, anything on site.
- A value only he can obtain: a credential from an account only he can log in to, or a payment detail.
- Spending money or starting a paid service.
- Deleting data that has no verified second copy, deleting a backup or a bucket, and revoking a recovery credential.
- Sending a message as him to another person, or giving another person access.

Everything else is yours to decide. Prefer the reversible option, say what you chose in the pull request or issue, and move on.

### Engineering care

- **Secrets.** Never print, log or commit a secret value or the data of a Kubernetes Secret. Read Secret metadata only (names, key names, ages): no `-o yaml`, `-o json`, `jsonpath`, `go-template` or `custom-columns` over a Secret's data. Git holds SOPS ciphertext only. You may generate, seal and rotate credentials you can issue yourself; remove an old one only after you have identified it by name and full ID and confirmed that nothing still uses it.
- **Data.** Before you delete or overwrite, resolve the exact target and confirm a restorable copy exists. Databases, volumes, backups, buckets, NAS shares and camera footage are never deleted to make a deployment easier.
- **Checks in proportion to risk.** Use the cheapest check that proves the changed behaviour. Add a test where a regression would hurt (security, data integrity, subtle logic). Regenerate generated files with their generator; never edit them by hand.
- **Heavy work runs in the cluster.** Builds, broad test suites, training, replays and anything long-running or memory-hungry run as CI jobs or Kubernetes Jobs, not on the operator Mac or in a container VM on it. The Mac is for editing, orchestration and small checks.
- **Leave it clean.** Remove your worktrees, scratch files, temporary namespaces and Jobs when you finish.

## Using the estate

The estate is one k3s cluster named `vallery` (three control-plane VMs and five GPU worker VMs on a six-host Proxmox cluster), a Synology NAS, a UniFi network and Cloudflare in front. The full guide, with commands for everything below, is [`docs/estate/GUIDE.md` in jvallery/estate-platform](https://github.com/jvallery/estate-platform/blob/main/docs/estate/GUIDE.md).

**Access.** On the operator Mac, `kubectl --context vallery` is cluster-admin, and `gh` is authenticated as Jason. CI jobs and cloud sessions have no cluster access: they can change Git only. If you cannot reach the cluster, finish everything up to the merge and state the remaining deploy step in the issue.

**Who owns what.** Every namespace, hostname, device, bucket and job has exactly one owner repository, recorded in `ownership/record/` in jvallery/estate-platform and rendered to [`ESTATE-INVENTORY.md`](https://github.com/jvallery/estate-platform/blob/main/docs/estate/ESTATE-INVENTORY.md). A namespace's label `estate.vallery.net/owner` names its owner. When you add, move or retire something, update the record in the same piece of work.

| Area | Owner |
|---|---|
| Argo CD, the application catalog, shared operators (CNPG, cert-manager, MariaDB), Authentik, central secrets, ownership record, standards | `jvallery/estate-platform` |
| CI workflows and runners | `jvallery/estate-ci` |
| DNS, routes, TLS, Cloudflare, UniFi, Pi-hole, MetalLB, Traefik | `jvallery/network-infra` |
| Longhorn, StorageClasses, NAS shares, S3 buckets, the image registry, backups | `jvallery/storage-infra` |
| Prometheus, Alertmanager, Loki, Grafana, probes, every alert rule and dashboard | `jvallery/monitoring-stack` |
| Proxmox hosts, VMs, k3s nodes, node labels and taints | `jvallery/proxmox-infrastructure` |
| GPUs, GPUStack, the Cortex inference API, Open WebUI | `jvallery/cortex-ai-compute` |
| The operator Mac and this contract | `jvallery/home-dotfiles` |
| Each application | Its own repository, which owns what renders into its namespaces |

**Deploying.** Argo CD applies what is in Git, read through an in-cluster mirror that trails GitHub by one to four minutes. For many Applications, including most secret bundles in jvallery/estate-platform, merging is the deploy event. An Application that is manual or pinned has its reason on its row in `gitops/rings.yaml` in jvallery/estate-platform. Look up how your application is delivered:

```sh
kubectl --context vallery get applications.argoproj.io -A --field-selector metadata.name=<app> -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,TARGET:.spec.source.targetRevision,AUTO:.spec.syncPolicy.automated,REV:.status.sync.revision,SYNC:.status.sync.status,HEALTH:.status.health.status
```

- `TARGET` is `main` and `AUTO` is on: merging deploys. Wait until `REV` is your merge commit or a later one and the application is Synced and Healthy. `AUTO` is off when it shows `<none>` or `enabled:false`.
- `TARGET` is `main` and `AUTO` is off: merge, then sync the objects you changed, and only those, unless this repository's own section says its application must be synced whole.
- `TARGET` is a commit SHA: the application is pinned, and its `pin_reason` in `gitops/rings.yaml` says what the pin protects. Move the pin to your merge commit in the file that declares the Application (`gitops/applications/<environment>/` in jvallery/estate-platform, or `argocd/` in the owning repository), merge that, then sync.

The guide has the exact commands for a sync and a pin move. Two rules protect the cluster. Sync named objects, never a whole pinned parent application, because a whole sync applies every child in the pin range and can roll back other people's work. Never sync with prune, because pruning is off everywhere on purpose and a prune deletes every object Git no longer lists, volumes and namespaces included.

**CI and images.** CI is GitHub Actions on in-cluster runners owned by jvallery/estate-ci; GitHub-hosted runners are not allowed. A repository calls the reusable workflows in jvallery/estate-ci by full commit SHA. Images are built in CI with Kaniko and pushed to `registry.vallery.net`. In most repositories the build then opens a pull request that pins the new digest in the deployment manifests, and merging that pull request is the release; where a repository promotes differently, its own section says how. Manifests reference images by `@sha256` digest only. Upstream images are mirrored into the registry first. Re-run a failed job with `gh run rerun <run-id> --failed`.

**Secrets.** A Kubernetes Secret exists in Git only as a SOPS-encrypted file (`secret-<name>.enc.yaml`), decrypted in the cluster by Argo CD. Most live in `secrets/<bundle>/` in jvallery/estate-platform; storage and network secrets live in their own repositories. Encrypting needs only the public keys in `.sops.yaml`, so you can add or rotate a secret yourself: write the manifest, run `sops --encrypt --in-place <file>`, list it in the bundle's `secret-generator.yaml`, and deliver it like any other change. For a value only Jason can obtain, prepare everything else and open a `needs-jason` issue.

**Running workloads.** Namespaces enforce the Pod Security `baseline` level by default. A new namespace needs the `estate.vallery.net/owner=<org>.<repo>` label, the LimitRange and ResourceQuota baseline, a default-deny NetworkPolicy and an ownership record entry. For one-off heavy work, use a CI job, a Job in the owning namespace, or a disposable namespace named `tmp-<topic>` and labelled `estate.vallery.net/disposable=true` and `estate.vallery.net/expires=<date>`, and delete it afterwards. The `tmp-` name matters: alert rules that read logs can tell a throwaway namespace only by its name. Give Jobs a `ttlSecondsAfterFinished` and never create bare Pods, which block node drains. Cluster-scoped RBAC is written only through Git and Argo CD: a ClusterRole or ClusterRoleBinding created or deleted by hand pages as a critical security alert, so test with a namespaced Role and RoleBinding.

**Storage and databases.** The default StorageClass is replicated Longhorn. Use `longhorn-v1-rwo` for application data, `longhorn-v1-critical-rwo` for data that must survive a host loss, `longhorn-v1-rebuildable-rwo` for caches and scratch, and `nfs-rwx` only for NAS shares that jvallery/storage-infra has exported. S3 buckets come from jvallery/storage-infra. A Postgres database is a three-instance CloudNativePG cluster with backups to in-cluster S3 and to Backblaze B2; copy an existing one. Every durable volume needs a backup or a named exemption in jvallery/storage-infra.

**Exposing a service.** Add a Traefik `IngressRoute` in your namespace with `tls: {}`; the wildcard certificate for `*.vallery.net` already covers it. For login, add the Authentik forward-auth middlewares. A public hostname also needs a row in the Cloudflare tunnel configuration in jvallery/network-infra, an entry in the edge registry in jvallery/estate-platform, and a probe in jvallery/monitoring-stack.

**Observability.** Alert rules, probes and dashboards live only in jvallery/monitoring-stack, and that application deploys on merge, so a merged rule can page within minutes. Alerts go to Alertmanager, which pushes to Home Assistant; there is no other notification path. Dashboards are generated, never hand-edited. When something you run can fail in a new way, add the metric and the alert.

**Node and host maintenance.** Never cordon or drain a node by hand: a bare drain once moved five database primaries at once and took down the control-plane leader. Use `scripts/k3s-planned-maintenance.py` in jvallery/proxmox-infrastructure, one node at a time. It moves database primaries off the node first, drains it, and marks the work as planned so that the right alerts stay quiet. Read its README first, and never run a script only to see its help text.

**AI inference.** Workloads call the in-cluster OpenAI-compatible API at `https://cortex.vallery.net/v1` with their own scoped key, issued by jvallery/cortex-ai-compute. Never use an admin credential or an outside provider key.

**Standards.** The target state for high availability, backups, security, exposure and CI is [`docs/estate/STANDARDS.md` in jvallery/estate-platform](https://github.com/jvallery/estate-platform/blob/main/docs/estate/STANDARDS.md). New work meets it; drift you find is fixed in the change that touches it or filed as an issue.
<!-- estate-contract:end -->

## This repository

- It owns namespace `weather` and everything `deploy/k8s` renders into it, including the CloudNativePG cluster `weather-pg` with its volumes and operator-generated Secrets; the hostname `weather.vallery.net`, whose routes are IngressRoutes in `weather`; and the images `registry.vallery.net/jvallery/weather-app`, `weather-web` and `weather-ci`. Each object and its state is listed in [OWNERSHIP.md](OWNERSHIP.md); update it when you add or retire an object.
- `app/weather/` is the Python package (capture, telemetry, pipeline, media, AI, search, API and the role entry points in `cli`). `app/migrations/` holds the Alembic migrations and `app/tests/` the pytest suites.
- `web/` is the React Router front end (TypeScript, Vitest, Playwright).
- `deploy/k8s/` is the Kustomize tree that Argo CD renders; `deploy/k8s/kustomization.yaml` holds the two application image digests.
- [docs/architecture.md](docs/architecture.md) is the design, [docs/operations/](docs/operations) has one runbook per component, and [docs/contracts/](docs/contracts) holds the API contracts and test vectors. `docs/requirements.md` and `docs/decisions/` are background; where they differ from the architecture document, the architecture document is right.

## Build, test, deploy, verify

**Local checks.** Run `scripts/ci.sh lint typecheck unit` and nothing heavier. Integration suites, the Playwright browser suite, image builds and media or render pilots run only in CI. Toolchain versions are in [docs/operations/development.md](docs/operations/development.md).

**Contracts.** API contracts are generated from the Pydantic models. After changing a model, run `scripts/ci.sh contracts --write` and commit the regenerated OpenAPI and TypeScript files; the lint stage fails when they are stale. A vector file belongs in `docs/contracts/` only if a test loads it.

**Required checks.** `workflow-lint / lint`, `application / application` (lint, typecheck, unit and integration shards of `scripts/ci.sh`), `browser / browser` (Playwright, three engines) and `manifests / manifests` (`ci/manifests.sh`). Pull requests from forks do not run on the runners; push the change as a branch of this repository to check it.

**Deploying.** The Argo CD Application `weather` tracks `main` and syncs automatically, so every merge that changes `deploy/k8s` is a deployment.

1. A manifest change is live a few minutes after its pull request merges.
2. A code change needs a new image. After the merge, `.github/workflows/build.yml` rebuilds the images in `ci/images.tsv` whose paths changed, pushes them by digest, and opens a pull request as `vallery-pin-bot` that sets the `images:` digests in `deploy/k8s/kustomization.yaml`. Merge that pull request when its checks are green: it is the release. Never edit those two digests by hand.
3. Database migrations run as the Job `weather-migrate`, an Argo CD sync hook at wave 1, before the workloads that use the database (wave 2 and later).
4. The application's Secrets are the bundle `secrets/weather` in jvallery/estate-platform, delivered by the Application `weather-secrets`, which tracks `main` and syncs automatically. A changed Secret is live a few minutes after its pull request merges there.

**Proving it.**

```sh
kubectl --context vallery -n argocd get application weather -o custom-columns=REV:.status.sync.revision,SYNC:.status.sync.status,HEALTH:.status.health.status
kubectl --context vallery -n weather get pods -o custom-columns='NAME:.metadata.name,IMAGE:.status.containerStatuses[*].imageID'
curl -fsS https://weather.vallery.net/api/healthz
```

`REV` must be the merge commit of the release, every running container's image must be the pinned digest, and the public endpoint must answer. Then probe the behaviour you changed. The full probe list is in [docs/operations/deploy.md](docs/operations/deploy.md). To roll back, revert the pin commit and let Argo CD sync.

**Finishing a change.** Anything that can be operated gets a section in its runbook under `docs/operations/`. A new failure mode gets a metric here and an alert or probe in jvallery/monitoring-stack.

**Pilots and restore drills.** Run pilots in a disposable namespace as the shared block describes. Restore drills are the exception: `scripts/restore-drill.sh` creates `weather-drill-*` resources inside `weather`, so that the read-only restore keys are used in place and never copied. A drill NetworkPolicy isolates them from production. Run one drill at a time and delete its resources afterwards.

## Hazards

- **Never delete the database or the volumes.** The cluster `weather-pg`, its volume claims, the claims `weather-staging` and `tempest-receiver-spool`, and the namespace carry `Prune=false,Delete=false`. Recorded telemetry cannot be collected again, so never remove any of them to make a deployment work.
- **Migrations are expand and contract only.** Migrations have run on the production database. Never rewrite one that has run; add a new one that chains on the newest head in `app/migrations/versions/`. Old and new application versions run side by side during a rollout, so a migration must not break the version that is still running.
- **Camera originals are private.** A public image is served only when it is at least 600 seconds old, checked at request time. Do not weaken or cache around that check: the cameras show a home.
- **Nothing identifying goes into this public repository.** Exact coordinates, camera addresses, station serial numbers, internal addresses and credentials stay out of Git, issues, pull requests and logs. Use synthetic fixtures with obvious placeholder values.
- **Cameras are polled, never driven by visitors.** A collector polls only its own camera's snapshot endpoint at its configured cadence, and a public request never causes camera access. The cameras belong to the security system (jvallery/sentinel) and extra load degrades it.
- **One pipeline per camera.** Each `pipeline-<camera>` Deployment is the single writer of its camera's staging directory, segments and catalog rows. Keep `replicas: 1` with the `Recreate` strategy; a second replica races the render and cleanup passes.
- **New workloads ship disabled.** Add a new workload with `replicas: 0`, or leave it out of the kustomization, until its Secrets, volumes and keys exist. The Application syncs automatically, so a manifest with a missing prerequisite makes the whole application Degraded.
- **Media storage is nearly full.** Originals, stills, previews and timelapses land on NAS-backed object storage and NFS, and that NAS volume runs close to its free-space floor. Before you enable a new media product or change a capture cadence or a retention period, work out the added GiB per day, compare it with the free space jvallery/storage-infra reports, and put the figure in the issue.
- **Inference is shared.** Other consumers use the same Cortex routes. Keep AI concurrency at 1 and stay within the limits of the scoped key.

## Neighbours

- jvallery/estate-platform: the Argo CD Applications `weather` and `weather-secrets` (`gitops/applications/local-staging/`), the Secret bundle `secrets/weather`, and the Authentik application that protects the editor.
- jvallery/network-infra: the Cloudflare tunnel row and DNS for `weather.vallery.net`, the forward-auth middlewares, and the network attachment `weather-tempest-vlan10` that the Tempest receiver uses (`deploy/multus/`).
- jvallery/storage-infra: the object storage buckets and keys, the NFS staging volume, and the Backblaze B2 buckets and keys for backups.
- jvallery/monitoring-stack: probes, alert rules and dashboards for Weather.
- jvallery/estate-ci: the runner sets `weather-ci`, `weather-build` and `weather-buildcheck` and the reusable workflows.
- jvallery/sentinel: the cameras and their view-only credentials. jvallery/homeassistant owns the Tempest hub.
- jvallery/cortex-ai-compute: the inference routes and the scoped key.
- jvallery/wordpress: the vallery.net homepage, which embeds the weather card.
