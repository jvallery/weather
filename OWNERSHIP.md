# Weather ownership

What this repository owns, where each item lives, and its current state. Central record: agents `control-plane/agent-fleet-control/registry/policy/estate-ownership/`. Update both in the same change set.

State as of 2026-10-24: **planned, not deployed.** Fill in each row's health and verification once its delivering issue lands.

| Item | Kind | Where | Delivered by | State |
|---|---|---|---|---|
| `jvallery/weather` | Repository (public, MIT) | GitHub | — | Exists; fleet registry `gh-1431367372` pending (agents#5366) |
| `weather` | Namespace (PSA restricted, quota, default-deny) | k3s `vallery` | WX-01 | Planned |
| `weather` | Argo Application (source `deploy/k8s`) | agents catalog / AppProject `weather` | agents#5366, WX-01 | Planned |
| `weather.vallery.net` | Hostname (backend + route owner) | IngressRoutes in `weather` via wildcard tunnel | WX-01, network-infra#775 | DNS wildcard resolves; returns Traefik 404 today |
| `weather-app`, `weather-web` | Images, digest-pinned | `registry.vallery.net/jvallery/` | WX-02, WX-01 | Planned |
| `weather-pg` | CNPG Cluster, 3 instances, `longhorn-v1-rwo`, 20Gi each | `weather` | WX-05 | Source prepared under `deploy/k8s/database/`, excluded until prerequisites close; not deployed |
| CNPG archive | `weather-pg-b2` ObjectStore + `weather-pg-daily` ScheduledBackup; direct B2 | `weather`, B2 bucket owned by storage-infra | storage-infra#1112, WX-05 | Provider/key capability custody verified; application backup/PITR not commissioned |
| `weather-media`, `weather-originals` | Garage buckets on garage-hdd + custody mirror | storage-infra pipeline | storage-infra#1107 | Planned |
| `weather-staging` | Static NFS PV/PVC (Obsidian share, quota) | storage-infra grants / `weather` | storage-infra#1106, WX-03 | Planned |
| Tempest receiver attachment | NetworkAttachmentDefinition (agents-owned contribution, VLAN 10 `vlan` plugin) + reserved IP | agents Multus / network-infra IPAM | agents#5368, network-infra#774, WX-04 | Planned |
| Collectors ×3, receiver, pipelines ×3, scheduler, intel, api, web | Workloads | `weather` | Slice issues | Planned |
| Weather monitoring | Scrape, probes, rules, dashboards | monitoring-stack | monitoring-stack#1072 + slice launches | Planned |
| Secrets (camera, B2, Garage, Cortex, DB) | KSOPS | agents `secrets-ksops/weather` | owner issues above | Planned |

Adjacent items this repo consumes but does not own: Tempest hub (homeassistant), cameras (sentinel), Cortex routes (cortex-ai-compute), `ak-forwardauth` (network-infra), vallery.net homepage (VerdifyConsultancy/wordpress).

Database objects rendered only when #6 commissions `deploy/k8s/database/`:
`NetworkPolicy/weather-pg`, `NetworkPolicy/weather-database-clients`, and
`ConfigMap/weather-database-policy` accompany the Cluster/ObjectStore/backup.
Their state is source prepared, not deployed. Production Cluster, ObjectStore,
namespace and data PVC protection remain in force. Reader-only `weather-drill-*`
Cluster/ObjectStore/NetworkPolicy/PVC resources are temporary Weather-owned
restore artifacts inside `weather`, labelled disposable with an expiry; they
are never added to Argo. See `docs/operations/database.md` for the exact limits
and cleanup evidence. Central namespace ownership is supplied by agents#5366;
all application objects inherit Weather ownership, while central Secrets remain
agents contributions and provider B2 identities remain storage-infra's.
