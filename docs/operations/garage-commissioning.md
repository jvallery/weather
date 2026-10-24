# Garage commissioning probes

Storage-infra#1107 provisions the two Weather buckets through the agents
Garage actuator. Its transient probe Jobs carry
`app.kubernetes.io/managed-by=garage-change-gate` and
`garage-provision.vallery.net/change-id`. The Weather foundation's default-deny
policy blocks these pods; web-egress does not select them.

`deploy/k8s/commissioning/garage-probe-egress.yaml` is deliberately excluded
from the foundation kustomization. It grants only those labelled probes DNS
to the existing kube-dns pods and HTTPS to the existing Traefik pods. It grants
no application egress, API access or direct camera access. Weather owns the
policy; storage-infra owns buckets/keys and agents owns the actuator.

The actuator's supported endpoint is `https://s3-hdd.vallery.net`. A read-only
DNS lookup from an existing cluster pod on 2026-10-24 matched that hostname to
the current `traefik-apps/traefik-apps` Service, with no public address in the
answer. The Service HTTPS port is 443; its Traefik websecure container target
is 8443. The namespace and pod selectors match the live objects. No private
address is recorded here. This observation is not proof that CNI egress works.

Before commissioning, recheck runtime DNS against the authoritative Service
without printing addresses. If it resolves to a public or different endpoint,
stop and revise this source in its owner; do not add a wildcard egress rule.
Do not change the immutable provisioning plan to an unsupported endpoint.

After the source and actuator prerequisites merge and required CI passes,
server-dry-run the exact policy, retain its UID after scoped creation, and run
one tokenless, non-root anonymous endpoint probe in Weather using the same
labels and pinned image as the actuator. Check current capacity/quota first;
use a bounded deadline and small resources. The probe must resolve the host,
validate its TLS certificate and receive the expected anonymous HTTP 403.
Do not mount provider credentials for this prerequisite probe. Retain only
terminal metadata and checksums, then remove only its disposable resources.
Do not mint keys unless this restricted path succeeds. If it fails, remove
only the exact temporary policy/probe resources and fix the source forward.

Only then run the merged actuator's functional verification with per-bucket
keys. Repeat after at least ten minutes, prove cross-bucket reader denials,
then complete central custody and restore-reader acceptance. Retain the
policy only for the bounded commissioning operation and delete its exact UID
when all probe Jobs are terminal. Leave the policy out of the foundation
kustomization; adding permanent app egress belongs to that app's commissioning
issue. Never delete buckets, data or key projections as probe cleanup.
