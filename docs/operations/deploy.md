# Deploying Weather

The release steps (merge, pin pull request, migration hook) and the required checks are in
[AGENTS.md](../../AGENTS.md). This page holds what AGENTS.md does not: how the build works, the
checks after a rollout, runtime configuration, the capacity envelope and rollback detail.

## Build

`.github/workflows/build.yml` (weather#223) builds the images in `ci/images.tsv` whose inputs
changed. On a pull request each one builds with `push: false` on the credential-free
`weather-buildcheck` runner set, and `mirrors.txt` is checked. On `main`, `mirrors.txt` is copied
into the registry first (the `weather-python-base` and `weather-node-base` bases are
digest-preserving mirrors of the upstream images), then the images build on `weather-build` and are
pushed by digest. The pipeline `WEATHER_IMAGE_DIGEST` env follows the `weather-app` pin through the
kustomization's `replacements`, so it needs no edit. Image builds, integration tests, browser suites
and SBOM generation run only in CI, never on a workstation.

## After a rollout

The Argo CD Application `weather` must report the release's merge commit as its sync revision,
`Synced` and `Healthy`. Both API and web Deployments must have two available replicas, and every
running container's image ID must match its pinned digest. A merged commit or a completed build
alone does not prove a deployment.

Probe the public root and `/api/healthz`, including HSTS. Public `/metrics` must return 404. From
the monitoring namespace, API and web port 9000 return Prometheus text and the API Service on port
80 serves `/readyz` (container port 8000). The web Service also exposes HTTP port 80, targeting
container port 3000, matching its forward-auth provider's internal address. Check that Pod Security
admission rejects a root pod, that all Weather pods are tokenless, and that the label-restricted
network paths deny an unrelated pod access to the API and deny API internet egress. Use disposable,
bounded probe pods and remove them afterwards.

Cloudflare browser-integrity checking rejects the default Python client signature with error 1010,
while curl and the Blackbox Exporter pass. Compare those paths when diagnosing an edge denial. In an
API egress check, kube-router's ICMP policy rejection returns ECONNREFUSED, which must not be
described as a timeout.

The web route has priority 10, above the shared catch-all at 1 and below the API route at 100.
Equal priorities once made one Traefik replica select the static 404 backend and caused intermittent
public 404s; after changing a priority, check every ingress replica.

## Runtime configuration

API and web disable Kubernetes Service environment injection with `enableServiceLinks: false`. This
keeps `WEATHER_PG_*` service variables out of the strict `WEATHER_*` application settings; clients
use Service DNS.

The API requires `WEATHER_PUBLIC_ORIGIN=https://weather.vallery.net`; `PORT` and `METRICS_PORT`
default to 8000 and 9000 and must differ. Unknown `WEATHER_*` runtime configuration keys, missing
origins, placeholder origins and invalid ports fail before either server binds. Startup errors name
only the fields; do not print Pydantic's raw validation errors or environment. The `WEATHER_CI_`
prefix is reserved for the check runner and its pinned tools.

## Capacity envelope

Quota is an admission ceiling, not a reservation or permission to consume unused resources. Before
enabling a workload or running a drill, inspect current node allocatable resources, scheduled
requests and limits, working sets, quotas and pressure. Do not alter node labels or tolerations to
force placement.

The namespace budget is 12 requested CPU / 20 GiB requested memory, 40 CPU / 64 GiB limits,
500 GiB PVC requests, 16 PVCs and 40 pods. Planned profiles (budgets set before launch, not measured
throughput; qualify a profile before relying on it):

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

The envelope leaves 5.2 requested CPU, 7.25 GiB requested memory, 5.5 CPU limits and 13 GiB memory
limits inside quota. Reserve 60 GiB for the three production database volumes, 1 GiB for the
receiver spool, 20 GiB for the drill database and up to 50 GiB for drill Garage, plus the static NFS
staging PVC's 300 GiB request: 431 GiB total, leaving 69 GiB. Static NFS claims still count against
the namespace PVC-request quota; physical NFS usage has its own storage quota. Run one restore drill
at a time and keep production and shared volumes throughout cleanup.

## Rollback and recovery

Note the previous pin commit and both digests before a release. To roll back, revert only the pin
commit, let Argo CD apply it, then repeat the revision, image-ID and public probes. Do not delete the
namespace, database, PVCs, PVs or buckets to redeploy. Database changes use expand and contract
migrations; restoring data is in [database.md](database.md) and [recovery.md](recovery.md).

If the Application is Degraded on a missing prerequisite, leave the new workload disabled and say
which prerequisite is missing in its issue. Investigate crash loops with sanitized logs;
configuration failures may identify a field but must never print its value. No startup path may
download packages, scripts or fonts.
