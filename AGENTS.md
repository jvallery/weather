# weather

The public weather service at `weather.vallery.net`: Tempest station telemetry and history, timelapses, keograms, hourly stills and delayed previews from three cameras, a local almanac, AI descriptions, search and a private editor. It runs on Kubernetes and is deployed from `main` of this repository by Argo CD.

This repository is public. Everything in it, and in its issues and pull requests, is readable by anyone. This file is the guide for every contributor and coding agent; it needs no access beyond this repository.

## This repository

- `app/weather/` is the Python package (capture, telemetry, pipeline, media, AI, search, API and the role entry points in `cli`). `app/migrations/` holds the Alembic migrations and `app/tests/` the pytest suites.
- `web/` is the React Router front end (TypeScript, Vitest, Playwright).
- `deploy/k8s/` is the Kustomize tree that Argo CD renders into namespace `weather`; `deploy/k8s/kustomization.yaml` holds the two application image digests. `deploy/acceptance/` holds the restore-drill and acceptance objects.
- [docs/architecture.md](docs/architecture.md) is the design, [docs/operations/](docs/operations) has one runbook per component (index: [docs/runbooks/README.md](docs/runbooks/README.md)), and [docs/contracts/](docs/contracts) holds the API contracts and test vectors. `docs/requirements.md` and `docs/decisions/` are background; where they differ from the architecture document, the architecture document is right.
- The deployment's surroundings (the Argo CD Application definitions, Secret values, DNS and the public tunnel, object storage buckets, alert rules and dashboards, the cameras and the inference service) are kept by the maintainer outside this repository. You do not need them to build, test or contribute.

## Build, test, deploy, verify

**Set up.** Toolchain versions are in [docs/operations/development.md](docs/operations/development.md). Run `uv sync --frozen`, then `cd web && pnpm install --frozen-lockfile --ignore-scripts`.

**Local checks.** Run `scripts/ci.sh lint typecheck unit` and nothing heavier. Integration suites, the Playwright browser suite, image builds and media or render pilots run only in CI.

**Contracts.** API contracts are generated from the Pydantic models. After changing a model, run `scripts/ci.sh contracts --write` and commit the regenerated OpenAPI and TypeScript files; the lint stage fails when they are stale. A vector file belongs in `docs/contracts/` only if a test loads it.

**Contributing.** Work items are this repository's GitHub issues. Make the smallest complete change on a branch, open a pull request that names its issue, and let the required checks run. A pull request merges when they are green.

**Required checks.** `workflow-lint / lint`, `application / application` (lint, typecheck, unit and integration shards of `scripts/ci.sh`), `browser / browser` (Playwright, three engines) and `manifests / manifests` (`ci/manifests.sh`, which renders and validates `deploy/k8s`). They run on self-hosted runners through the reusable workflows of [jvallery/estate-ci](https://github.com/jvallery/estate-ci), which `.github/workflows/ci.yml` and `build.yml` call by commit SHA. Pull requests from forks do not run on those runners: a maintainer reviews a fork's change and pushes it as a branch of this repository to check it.

**Releases.** Argo CD renders `deploy/k8s` from `main`, so a merge that changes `deploy/k8s` is a deployment.

1. A manifest change is live a few minutes after its pull request merges.
2. A code change needs a new image. After the merge, `.github/workflows/build.yml` rebuilds the images in `ci/images.tsv` whose paths changed, pushes them by digest, and opens a pull request as `vallery-pin-bot` that sets the `images:` digests in `deploy/k8s/kustomization.yaml`. Merging that pull request is the release. Never edit those two digests by hand. Image bases are digest-preserving mirrors listed in `mirrors.txt`.
3. Database migrations run as the Job `weather-migrate`, an Argo CD sync hook at wave 1, before the workloads that use the database (wave 2 and later).

**Checking a release.** `curl -fsS https://weather.vallery.net/api/healthz` must answer, and the behaviour you changed must show on the public site. The full probe list, the rollout checks and the capacity envelope are in [docs/operations/deploy.md](docs/operations/deploy.md). To roll back an image, revert the pin commit; Argo CD applies the revert.

**Finishing a change.** Anything that can be operated gets a section in its runbook under `docs/operations/`. A new failure mode gets a metric here, so that an alert can be written against it.

**Restore drills.** `scripts/restore-drill.sh` creates `weather-drill-*` resources inside namespace `weather`, so that the read-only restore keys are used in place and never copied. A drill NetworkPolicy isolates them from production. Run one drill at a time and delete its resources afterwards. The database runbook is [docs/operations/database.md](docs/operations/database.md); media recovery is [docs/operations/recovery.md](docs/operations/recovery.md).

## Hazards

- **Never delete the database or the volumes.** The cluster `weather-pg`, its volume claims, the claims `weather-staging` and `tempest-receiver-spool`, and the namespace carry `Prune=false,Delete=false`. Recorded telemetry cannot be collected again, so never remove any of them to make a deployment work.
- **Migrations are expand and contract only.** Migrations have run on the production database. Never rewrite one that has run; add a new one that chains on the newest head in `app/migrations/versions/`. Old and new application versions run side by side during a rollout, so a migration must not break the version that is still running.
- **Camera originals are private.** A public image is served only when it is at least 600 seconds old, checked at request time. Do not weaken or cache around that check: the cameras show a home.
- **Nothing identifying goes into this repository.** Exact coordinates, camera addresses, station serial numbers, internal addresses, host names, credentials and raw operational logs stay out of Git, issues, pull requests and logs. Use synthetic fixtures with obvious placeholder values.
- **Cameras are polled, never driven by visitors.** A collector polls only its own camera's snapshot endpoint at its configured cadence, and a public request never causes camera access. The cameras belong to a home security system and extra load degrades it.
- **One pipeline per camera.** Each `pipeline-<camera>` Deployment is the single writer of its camera's staging directory, segments and catalog rows. Keep `replicas: 1` with the `Recreate` strategy; a second replica races the render and cleanup passes.
- **New workloads ship disabled.** Add a new workload with `replicas: 0`, or leave it out of the kustomization, until its Secrets, volumes and keys exist. Argo CD syncs `main` automatically, so a manifest with a missing prerequisite makes the whole application Degraded.
- **Media storage is nearly full.** Originals, stills, previews and timelapses land on NAS-backed object storage and NFS that run close to their free-space floor. Before you enable a new media product or change a capture cadence or a retention period, work out the added GiB per day and put the figure in the issue.
- **Inference is shared.** Other consumers use the same inference routes. Keep AI concurrency at 1 and stay within the limits of the scoped key.

## Neighbours

- [jvallery/estate-ci](https://github.com/jvallery/estate-ci) (public): the reusable workflows and the runner sets `weather-ci`, `weather-build` and `weather-buildcheck`.
- Everything else this service depends on (Argo CD, the Secret bundle, DNS and the tunnel, forward-auth for the editor, buckets and NFS, monitoring, the cameras, the Tempest hub, the inference routes and the vallery.net homepage that embeds the weather card) is maintained outside this repository. An issue here that needs a change there says so; the maintainer carries it.
