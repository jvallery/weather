# D04 — direct Tempest reception through isolated secondary L2 attachments

> **Historical.** Binding rules live in [docs/architecture.md](../architecture.md) and the 2026-10-24 [simplification](2026-10-24-simplification.md) and [takeover](2026-10-24-takeover-replan.md) records; where this file conflicts, those win.

> **Amended 2026-10-24** by the [2026-10-24 simplification record](2026-10-24-simplification.md): P4: one receiver on a single macvlan NAD (agents#5368, network-infra#774). Dual receivers and the custom fail-closed CNI guard are **withdrawn**; the residual same-L2 risk is documented. Where this ADR conflicts with that record or [architecture.md](../architecture.md), they win.

Status: **attachment architecture selected; CNI/isolation admission and pod-level qualification pending**. Requirements R02/R08/R13/R14/R16/R27/R28; Weather consumers WX-04/11/12. Owners: [OWN-UDP](https://github.com/jvallery/network-infra/issues/774) and [OWN-L2](https://github.com/jvallery/agents/issues/5368).

## Decision

Receive native station broadcasts through dedicated Multus VLAN interfaces on the two existing observation-trunk candidates. Preserve each pod's primary cluster interface for bounded spool/database communication. The receiver uses an ordinary UDP socket, runs nonroot with capabilities dropped, and neither changes the station nor depends on Home Assistant, the existing greenhouse parser or a cloud API.

Use two explicitly admitted receiver placements with disjoint reserved addresses and persistent receipt identities. Each records its own received bytes/provenance; canonical normalization deduplicates observations/events without deleting receipt history. This is not permission to claim complete station delivery: UDP can be lost before either receiver sees it.

The VLAN CNI plugin and a fail-closed secondary-interface guard are owner prerequisites. A Multus daemon being Ready is not proof they exist. No new network resource has been deployed in this planning pass.

## E03: evidence changing the choice

On 2026-10-23, bounded read-only inspection found Multus Ready on eight nodes and zero NetworkAttachmentDefinitions. The guest-owner source records two addressless observation trunks already carrying the station VLAN on separate workers. One inspected guest confirmed the trunk up.

Two separate 20-second, non-promiscuous header-summary captures on those trunks observed the expected station source sending to the IPv4 limited-broadcast destination on UDP port 50222. One captured nine packets with ten reported by the filter; the other captured ten with ten reported. Both reported zero kernel drops. Both ended at their deliberate timeout. This proves recent traffic at the candidate guest interfaces, not complete reception, a packet-loss rate, payload parsing, stable long-term cadence or pod reachability. The captures were sequential, not a synchronized redundancy test.

Source UDP ports changed between packets. Filter on the destination port and admitted source identity, not a fixed source port. Payload lengths differed; no message-family inference is made from length. No full datagrams were retained for parser qualification during this header-only observation.

Private structured receipt E03 is retained under OWN-UDP; SHA-256 `7ad9eb57e85f48d4d22b68ccc3a824f1a4bf4517510ec30fd2a303581301966c`. Source revisions: guest inventory `5f0d438dc7f10b0aad88d6375a3e68644e65c234`, network inventory `19649acecf2ff9a4f23e26a1810f1ef519d8a4b1`, Verdify reference `3b2516fe0b0d7cbc55ffd867d7b5cba64a39f54e`. Exact interfaces, addresses and capture metadata remain private.

The inspected CNI path has host-local and firewall executables but lacks vlan, macvlan, ipvlan and static executables; alternate conventional locations checked on that worker did not supply them. Availability on all eventual placement nodes remains an implementation preflight requirement.

## Reference findings and alternatives

The [Tempest UDP reference](https://weatherflow.github.io/Tempest/api/udp/v171/) specifies local broadcasts to destination port 50222. A routed ClusterIP/LoadBalancer or ordinary hostNetwork pod on a different VLAN does not establish reception.

The Verdify firmware reference intentionally processes only obs_st and its first observation row; the historical sync script polls selected HA projections. Neither meets Weather's every-received-payload/all-row requirement. Preserve their existing behavior; use only reviewed protocol ideas, not their omissions or credential model.

[K3s Multus guidance](https://docs.k3s.io/networking/multus-ipams) distinguishes the primary CNI from secondary attachments and requires IPAM. Host-local allocation alone is node-local, so overlapping pools across nodes are not an acceptable address plan. The [VLAN CNI contract](https://www.cni.dev/plugins/current/main/vlan/) requires a distinct master/VLAN pair for each container on a node. These sources were reviewed 2026-10-23; qualify installed versions before deployment.

| Alternative | Disposition |
|---|---|
| Reuse HA states or Verdify normalized weather | Rejected as canonical ingestion: loses payloads, fields, native arrival cadence and original provenance. |
| Gateway broadcast relay | Not selected while the existing trunks already observe native packets; adds a gateway service and forwarding failure/trust boundary. |
| New VLAN trunk/VM NIC | Not the initial choice; existing candidates already see broadcasts. Reconsider only if owner admission shows they cannot safely be reused. |
| Privileged/hostNetwork raw-packet collector | Not selected for the application; unnecessarily exposes host interfaces and permissions. Header diagnostics do not authorize that runtime design. |
| Bare NAD without secondary policy | Rejected: primary network policy and absence of a default route do not prove isolation from same-L2 devices. |

## Exact implementation contract

Network owns station/source allowlists, IP reservations, VLAN policy and exact attachment admission. Agents owns the shared CNI binary/setup path and central admission/ownership declarations. The guest owner retains observation-trunk configuration; any needed change goes through that owner. Weather owns the receiver logic and its data contracts. No existing ARP observer, camera guard or HA receiver may be repurposed.

Each receiver placement has a distinct reserved address and a node-scoped attachment record. Host-local IPAM, if selected, uses disjoint owner-reserved single-address ranges tied to the admitted node; never the same unconstrained pool on two hosts. No secondary default route, DNS override or router advertisement is installed. Preserve the primary interface's ordinary DNS/database policies.

Enforce one active container per master/VLAN pair on a node. Use non-overlapping replacement and explicit placement; CNI failure leaves the receiver unavailable rather than attaching a conflicting duplicate. Keep the pair's cleanup bound to exact namespace/interface identity. Retained spool identity and replay state survive replacement independently of temporary CNI state.

The application enables IP_PKTINFO and validates ingress interface/destination metadata from recvmsg, in addition to source allowlists and payload validation. It must not require raw sockets or runtime network-administration capability merely to distinguish interfaces. Primary-interface ingress to the receiver's UDP port remains denied.

The CNI-owned setup guard is installed in the target network namespace before container execution: allow admitted-source broadcast UDP to the station destination port on the secondary interface; deny other secondary ingress, secondary egress and forwarding, including IPv6. Preserve legitimate primary-interface traffic. Setup failure fails CNI ADD and removes its attachment; no briefly unguarded application start is acceptable. Teardown cannot remove another pod's or host's rules.

The generic [CNI firewall plugin](https://www.cni.dev/plugins/current/meta/firewall/) is not sufficient evidence: its documented purpose is allowing container traffic through host rules, and its defaults can broaden access. OWN-L2 must implement/qualify the exact narrow namespace guard using the supported owner lifecycle. Do not copy the existing host camera guard into a different packet path and assume equivalence.

Source IP/MAC allowlists and private hub/device identities support validation, but Tempest UDP is not cryptographically authenticated. A same-LAN spoof remains a trust limitation. Preserve rejected/unknown/malformed provenance according to the private ingest policy and make clock/source discontinuities visible.

## Qualification and limits

Before starting a Weather receiver, owner evidence must show binary/version custody, reservations, supported Pod Security admission, correct CNI path on both placements, guard lifecycle, no interference with existing observers and bounded resource/spool admission. If the namespace guard cannot be supported, D04 remains blocked; do not silently switch to a gateway relay or broad hostNetwork.

Then test native full datagrams in each pod, all protocol families/rows, unknown fields, actual source/freshness timing, kernel socket overflow counters, duplicate/reordered delivery, database outage/spool replay, pod replacement, node loss, partition and recovery. Negative tests must attempt other secondary VLAN/LAN access, ingress from nonstation sources, IPv6 and guard bypass while verifying primary DNS/database access still works.

D05 owns receipt/canonical identity and independent durable spool sizing. Expired receiver leases do not delete uncommitted receipts. Two replicas plus repeated packets do not automatically prove no data loss or exactly-once events.

Rollback stops only Weather receiver consumers, reconciles retained spools, removes their exact attachment/guard/IP reservations through owners, and verifies existing trunk/observer/camera state. Do not uninstall shared Multus or change station/network behavior to remove Weather.

