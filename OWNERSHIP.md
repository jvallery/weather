# OWNERSHIP: jvallery/weather

Everything this repository owns, as listed in the estate ownership record in jvallery/estate-platform (`ownership/record/`). This file is a copy of the rendered page [docs/estate/inventory/jvallery__weather.md](https://github.com/jvallery/estate-platform/blob/main/docs/estate/inventory/jvallery__weather.md); the record is the authority.

When an item is added, moved or retired, change its row in the record, run `python3 ownership/render.py` there, and copy the rendered tables here in the same piece of work. How each item is built, deployed and checked is in [AGENTS.md](AGENTS.md).

## cnpg-backup-class

| Id | Name | Summary | Location | State | Status |
| --- | --- | --- | --- | --- | --- |
| `cnpg-backup-class:weather/prechange` | weather/cnpg-prechange Backup CRs | On-demand CNPG Backup CRs taken before a change (class cnpg-prechange, storage-infra#1611; 1 live on 2026-11-17). Owned by the namespace owner; retention and per-CR disposition are storage-infra policy (docs/architecture/backup-policy.md, inventory/cnpg-prechange-backups.yaml). Deleting a CR leaves the base backup and WAL in the object store; delete a CR only by exact name and UID. Argo orphan warnings are ignored by name pattern in the AppProject (estate-platform#568). | k3s vallery / ns weather | running | `owned` |

## endpoint

| Id | Name | Summary | Location | State | Status |
| --- | --- | --- | --- | --- | --- |
| `endpoint:weather.vallery.net` | weather.vallery.net | Walking skeleton; live Tempest not commissioned. Weather backend and route; network-infra DNS/tunnel; Authentik editor. | weather/web | running | `owned` |

## k8s-workload

| Id | Name | Summary | Location | State | Status |
| --- | --- | --- | --- | --- | --- |
| `k8s-workload:weather/deployment/api` | api | Deployment api in namespace weather, declared in deploy/k8s/application.yaml. | namespace weather | running | `owned` |
| `k8s-workload:weather/deployment/pipeline-frontyard-1` | pipeline-frontyard-1 | Deployment pipeline-frontyard-1 in namespace weather, declared in deploy/k8s/pipeline/deployments.yaml. | namespace weather | running | `owned` |
| `k8s-workload:weather/deployment/pipeline-skycam-east` | pipeline-skycam-east | Deployment pipeline-skycam-east in namespace weather, declared in deploy/k8s/pipeline/deployments.yaml. | namespace weather | running | `owned` |
| `k8s-workload:weather/deployment/pipeline-skycam-west` | pipeline-skycam-west | Deployment pipeline-skycam-west in namespace weather, declared in deploy/k8s/pipeline/deployments.yaml. | namespace weather | running | `owned` |
| `k8s-workload:weather/deployment/scheduler` | scheduler | Deployment scheduler in namespace weather, declared in deploy/k8s/scheduler/scheduler.yaml. | namespace weather | running | `owned` |
| `k8s-workload:weather/deployment/tempest-receiver` | tempest-receiver | Deployment tempest-receiver in namespace weather, declared in deploy/k8s/receiver/receiver.yaml. | namespace weather | running | `owned` |
| `k8s-workload:weather/deployment/weather-preview` | weather-preview | Deployment weather-preview in namespace weather, declared in deploy/k8s/preview/preview.yaml. | namespace weather | running | `owned` |
| `k8s-workload:weather/deployment/web` | web | Deployment web in namespace weather, declared in deploy/k8s/application.yaml. | namespace weather | running | `owned` |
| `k8s-workload:weather/statefulset/collector-frontyard-1` | collector-frontyard-1 | StatefulSet collector-frontyard-1 in namespace weather, declared in deploy/k8s/capture/frontyard-1.yaml. | namespace weather | running | `owned` |
| `k8s-workload:weather/statefulset/collector-skycam-east` | collector-skycam-east | StatefulSet collector-skycam-east in namespace weather, declared in deploy/k8s/capture/skycam-east.yaml. | namespace weather | running | `owned` |
| `k8s-workload:weather/statefulset/collector-skycam-west` | collector-skycam-west | StatefulSet collector-skycam-west in namespace weather, declared in deploy/k8s/capture/skycam-west.yaml. | namespace weather | running | `owned` |

## namespace

| Id | Name | Summary | Location | State | Status |
| --- | --- | --- | --- | --- | --- |
| `namespace:weather` | weather | Weather namespace guardrails (PSA restricted, quota and default-deny); application commissioning pending. | k3s vallery | running | `owned` |

## pvc

| Id | Name | Summary | Location | State | Status |
| --- | --- | --- | --- | --- | --- |
| `pvc:weather/tempest-receiver-spool` | tempest-receiver-spool | PVC 1Gi class longhorn-v1-rwo. | namespace weather | running | `owned` |
| `pvc:weather/weather-staging` | weather-staging | PVC 300Gi class static volume weather-staging. | namespace weather | running | `owned` |

## registry

| Id | Name | Summary | Location | State | Status |
| --- | --- | --- | --- | --- | --- |
| `registry:zot/jvallery/weather-app` | registry.vallery.net/jvallery/weather-app | Walking skeleton API image running; live Tempest not commissioned. | zot origin registry.vallery.net | running | `owned` |
| `registry:zot/jvallery/weather-ci` | registry.vallery.net/jvallery/weather-ci | Weather disposable PostgreSQL integration tooling image, built in-cluster by its registry-declared Kaniko profile; not a production workload. | zot origin registry.vallery.net | declared-not-deployed | `owned` |
| `registry:zot/jvallery/weather-node-base` | registry.vallery.net/jvallery/weather-node-base | Weather immutable application image or upstream base import, built in-cluster by registry-declared Kaniko profile. | zot origin registry.vallery.net | declared-not-deployed | `owned` |
| `registry:zot/jvallery/weather-python-base` | registry.vallery.net/jvallery/weather-python-base | Weather immutable application image or upstream base import, built in-cluster by registry-declared Kaniko profile. | zot origin registry.vallery.net | declared-not-deployed | `owned` |
| `registry:zot/jvallery/weather-web` | registry.vallery.net/jvallery/weather-web | Walking skeleton web image running; live Tempest not commissioned. | zot origin registry.vallery.net | running | `owned` |

## repo

| Id | Name | Summary | Location | State | Status |
| --- | --- | --- | --- | --- | --- |
| `repo:jvallery/weather` | jvallery/weather | Public Weather application; ci-only fleet cell without a coding pod. | github.com/jvallery/weather | declared-not-deployed | `owned` |

## Applications rendered for this repo

Declared by another repo: the declaring repo owns the Application object, and this repo owns what it renders.

| Id | Name | Declaring owner | State | Status |
| --- | --- | --- | --- | --- |
| `argo-app:argocd/weather` | weather | `jvallery/estate-platform` | running | `owned` |
| `argo-app:argocd/weather-secrets` | weather-secrets | `jvallery/estate-platform` | running | `owned` |
