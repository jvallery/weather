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

`custody-probe-egress.yaml` is a separate, excluded policy for the independent
B2 restore-reader proof in storage-infra#1107. It reuses the prepared database
restore isolation pattern: DNS to kube-dns plus TCP 443 for dynamic B2
addresses. Native NetworkPolicy has no FQDN selector; this is a documented TLS
port exception, not an endpoint allowlist enforced by the network. The probe
must separately allow only the source-declared B2 HTTPS origin, validate its
certificate, and use only `weather-custody-restore-reader` in place. It mounts
no provider management identity or ServiceAccount token. No API endpoint port
is needed. Do not reuse the native controller's configured proxy or create a
new proxy, identity or RBAC for this probe.

The policy selects only pods with all three: managed-by
`weather-custody-probe`, `estate.vallery.net/disposable=true`, and a
`weather.vallery.net/operation` label. Use a unique `weather-drill-*` Job name,
future expiry, small bounded resources, no retries and a deadline. Keep the
foundation disabled until required Secrets exist. After merged source and
required CI, server-dry-run and create the exact policy, retain its UID, run
the reader-only Job, and delete only its terminal disposable resources and
that exact policy UID. Retain failed evidence and any unexpectedly created
probe object; never delete production data as failure cleanup.

A passing proof reads the two known seeded custody copies with exact hashes,
then shows write, delete and outside-prefix read denial. No response body,
credential, signed URL or request header belongs in output. Authorization
scope, central copy completion, observer state and actual restore permissions
are separate evidence. The central Garage-HDD full-copy window remains
06:00–08:00 UTC; this policy does not authorize bypassing it.
