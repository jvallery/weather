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
output against registry manifest metadata. After both verified digest pins are committed, register the fixed
`supply-chain-evidence` profile. It runs
`scripts/ci-evidence.sh` against trusted read-only OCI inputs and archives
`sbom-app.json`, `sbom-web.json` and `licenses.json`. Retain independent readback as part of WX-01 acceptance. Generic candidates
have no registry credentials.

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
Prometheus text and the API Service on port 80 serves `/readyz` (container
port 8000). The web Service also exposes HTTP port 80, targeting container
port 3000, matching its forward-auth provider's internal address.
Check PSA rejects a root pod, all Weather pods are tokenless, and the label-restricted network
paths deny an unrelated pod access to the API and deny API internet egress.
Use disposable, bounded probe pods and remove them after retaining evidence.

Run `python scripts/verify_foundation.py --revision <promotion-sha> --network
--output <receipt-path>` from the checked-out promotion. It compares rendered
pins, live image IDs, Argo revision/health, replica availability, public probes,
tokenless pods and network reachability. The optional network checks create
one untrusted pod in Weather and one metrics probe in `observability`, with a
45-second deadline each; both are deleted by exact name. The receipt contains
no private addresses. Retain it before closing WX-01. PSA root rejection and
API internet-egress denial are separate commissioning checks.

The public verifier identifies itself as `weather-foundation-probe/1.0`. During
initial commissioning, Cloudflare browser-integrity checking rejected the default Python client signature with error1010; the self-identifying probe, normal
curl and deployed Blackbox Exporter passed. Compare those paths when diagnosing
an edge denial. The API egress check uses a positive operator control connection
and generated policy counter changes: kube-router's ICMP policy rejection
returns ECONNREFUSED, which must not be described as a timeout.

Dated acceptance receipts are under `docs/operations/evidence/`. WX-01 still
requires durable SBOM upload/readback and final onboarding source closure; the
walking skeleton does not establish live Tempest conditions or database recovery.
Continuous monitoring subsequently observed intermittent public-edge HTTP404s;
that interrupts the launch probe window despite successful point-in-time checks.
The dated receipt records those failures until the network owner resolves them.

API and web disable Kubernetes Service environment injection with
`enableServiceLinks: false`. This keeps later `WEATHER_PG_*` service variables
out of the strict `WEATHER_*` application settings; clients use Service DNS.

The API requires `WEATHER_PUBLIC_ORIGIN=https://weather.vallery.net`; `PORT`
and `METRICS_PORT` default to 8000 and 9000 and must differ. Unknown
`WEATHER_*` runtime configuration keys, missing origins, placeholder origins
and invalid ports fail before either server binds. Startup errors name only
the fields; do not print Pydantic's raw validation errors or environment.
The `WEATHER_CI_` prefix is reserved for the check runner and its pinned tools.

## Supply-chain evidence

After both commissioned runtime digests exist, the registry-owned
`supply-chain-evidence` check runs `scripts/ci-evidence.sh` through the same
pinned fleet bootstrap. The fixed platform init authenticates with the existing read-only registry
identity and stages two hash-verified OCI layouts on a separate read-only
`/ci-input` volume. The candidate receives no registry credential. It checks the
input checkout revision, exact image pins, OCI manifest/configuration digest
chain, Linux amd64 selection and layer descriptors before serial Syft scans,
then stages `artifacts/supply-chain/sbom-app.json`, `sbom-web.json` and
`licenses.json`. Mutable tags, another repository's image, duplicate pins,
symlink inputs, duplicate JSON keys and output JSON files larger than 16 MiB
are rejected. OCI metadata files are bounded to 1 MiB. Fixed input paths come
from the platform contract, never a caller-selected path. The script
refuses to run image scans on the Mac, including when the CI flag is set.

The platform's fixed Weather evidence check must archive those three files
with a trusted manifest binding the Workflow/Pod UIDs and file SHA-256 values.
Verify that upload and an independent readback before accepting the files as
durable evidence. A local output directory, log line or completed scan alone
does not prove custody. Add this registry step only after the two image pins
exist; the scaffold's image-free supply-chain check runs before commissioning.

## Capacity envelope

Quota is an admission ceiling, not a reservation or permission to consume
unused resources. Before each commissioning or drill, inspect current node
allocatable resources, effective scheduled requests and limits, working sets,
quotas, pressure and fleet reservations. Keep control-plane nodes cordoned and
exclude the Frigate-dedicated worker. Do not alter node labels or tolerations
to force placement.

The namespace budget is 12 requested CPU / 20 GiB requested memory, 40 CPU /
64 GiB limits, 500 GiB PVC requests, 16 PVCs and 40 pods. Planned profiles:

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
for drill Garage, plus the static NFS staging PVC's 300 GiB request: 431 GiB
total, leaving 69 GiB. Static NFS claims still count against the namespace
PVC-request quota; physical NFS usage has its separate storage-owner quota.
Run one restore drill at a time;
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
