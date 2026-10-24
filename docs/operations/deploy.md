# Deploying Weather

Weather runs in context `vallery`, namespace `weather`. Argo Application
`weather` renders `deploy/k8s` from this repository. The fleet registry in
`jvallery/agents` owns the build profiles and generated CI configuration.

## Build and promote

Run lightweight lint, type checking and focused unit checks with
`scripts/ci.sh lint typecheck unit`. Submit image builds, integration tests,
browser suites and SBOM generation through fleet CI. Never run those on the
operator Mac. Fork pull requests do not execute in fleet CI; a maintainer must
review and re-push a same-repository branch.

Build both `weather-app` and `weather-web` at an immutable source revision.
Retain the terminal workflow results and image-ref/digest outputs. Verify each
output against registry manifest metadata. Run `scripts/ci.sh supply-chain
--image <ref>` in fleet CI for each image and retain `sbom-app.json`,
`sbom-web.json` and `licenses.json` with the build evidence.

A `deploy:` commit sets both entries in the root kustomization's `images`
list to the verified `registry.vallery.net/jvallery/weather-*` digests.
Enable a new workload only after its Secrets, volumes and image exist. During
initial commissioning, application and ingress definitions are excluded from
the root kustomization and Deployments have zero replicas. Once prerequisites
are verified, include both files and set the API and web replicas to two in the
same promotion.

Merge through the repository's enforced checks. Argo auto-syncs the promotion.
Inspect `kubectl --context vallery -n argocd get application weather -o json`:
the sync revision must equal the promotion SHA, sync must be `Synced`, and
health must be `Healthy`. Verify both Deployments have two available replicas
and every running container's `imageID` matches its promoted digest. A merged
commit or completed build alone does not prove deployment.

Probe the public root and `/api/healthz`, including HSTS. Public `/metrics`
must return 404. From `observability`, verify API/web port 9000 returns
Prometheus text and API port 8000 `/readyz` succeeds. Check PSA rejects a
root pod, all Weather pods are tokenless, and the label-restricted network
paths deny an unrelated pod access to the API and deny API internet egress.
Use disposable, bounded probe pods and remove them after retaining evidence.

## Capacity envelope

Quota is an admission ceiling, not a reservation or permission to consume
unused resources. Before each commissioning or drill, inspect current node
allocatable resources, effective scheduled requests and limits, working sets,
quotas, pressure and fleet reservations. Keep control-plane nodes cordoned and
exclude the Frigate-dedicated worker. Do not alter node labels or tolerations
to force placement.

The namespace budget is 12 requested CPU / 20 GiB requested memory, 40 CPU /
64 GiB limits, 200 GiB PVC requests, 16 PVCs and 40 pods. Planned profiles:

| Workload group | CPU request / limit | Memory request / limit |
|---|---|---|
| Two API and two web replicas | 0.4 / 4 | 0.5 / 2 GiB |
| Three database instances | 1.5 / 6 | 3 / 12 GiB |
| Receiver and scheduler | 0.2 / 1.5 | 0.25 / 1 GiB |
| Three collectors | 0.3 / 3 | 0.375 / 1.5 GiB |
| Three pipelines | 3 / 12 | 6 / 24 GiB |
| Intel and one migration | 0.2 / 2 | 0.375 / 1.5 GiB |
| Production subtotal | 5.6 / 28.5 | 10.5 / 42 GiB |
| One drill database | 0.5 / 2 | 1 / 4 GiB |
| Drill Garage plus API/web | 0.45 / 3 | 0.75 / 3 GiB |
| One bounded acceptance probe | 0.25 / 1 | 0.5 / 2 GiB |
| Combined envelope | 6.8 / 34.5 | 12.75 / 51 GiB |

These are commissioning budgets, not measured renderer throughput. Later
issues must qualify their profiles before enabling them. The envelope leaves
5.2 requested CPU, 7.25 GiB requested memory, 5.5 CPU limits and 13 GiB memory
limits inside quota. Reserve 60 GiB for the three production DB volumes,
1 GiB for the receiver spool, 20 GiB for the drill database and up to 50 GiB
for drill Garage: 131 GiB total, leaving 69 GiB. NFS staging is a static
volume with its separate storage-owner quota. Run one restore drill at a time;
retain production and shared volumes throughout cleanup.

## Rollback and recovery

Record the previous promotion SHA and both digests before deploying. Revert
only the promotion commit to restore those images, let Argo sync, then repeat
the revision, image-ID and public probes. Do not delete the namespace,
database, PVCs, PVs or buckets to redeploy. Database changes use expand/contract
migrations and have their own recovery procedure.

If Argo is blocked on a missing prerequisite, leave the new workload disabled
and record the prerequisite issue. Investigate crash loops with sanitized
logs; configuration failures may identify a field but must never print its
value. No startup path may download packages, scripts or fonts.
