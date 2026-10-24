# D07 — Canonical object storage, durability and cleanup eligibility

> **Amended 2026-10-24** by the [2026-10-24 simplification record](2026-10-24-simplification.md): O1/P6: `weather-media` and `weather-originals` on garage-hdd with custody mirrors. The owner local-durability receipt as a cleanup gate is **withdrawn**; cleanup requires content-verified readback plus a committed catalog row. Where this ADR conflicts with that record or [architecture.md](../architecture.md), they win.

Status: capacity-tier selection and receipt/cleanup protocol selected; accepted local-loss window, actual durability mechanism and owner capacity remain material decisions. Requirements §§6.8–6.9, 11.1–11.4 and 15; consumers WX-06/17/18/28/29/30/32/37/38. OWN-OBJECT supplies storage admission; D08 enforces access and D15 qualifies independent recovery. This ADR makes no provider change.

## Live evidence and its limits

E10 (2026-10-23) reads the running custom Garage binary version and exact image identity, the mounted TOML and bound PV metadata. The capacity-tier configuration uses replication factor one and SQLite metadata. Neither data_fsync nor metadata_fsync is explicit; no corresponding environment override appears in the pod specification. Metadata is on a Longhorn claim; object blocks are on the NAS-backed NFS claim with soft timeout mount options. These observations do not measure the physical flush path or the durability of an acknowledged object.

The [Garage configuration reference](https://garagehq.deuxfleurs.fr/documentation/reference-manual/configuration/), checked 2026-10-23, documents false defaults for both fsync settings and maps SQLite metadata_fsync=true to synchronous=NORMAL. [SQLite's synchronous documentation](https://www.sqlite.org/pragma.html#pragma_synchronous) explains that WAL/NORMAL can lose recent commits after system/power failure. Consequently, even enabling both flags would not by itself establish zero acknowledged-write loss. The custom runtime and full client/NAS/disk stack require owner qualification; do not infer an effective runtime guarantee solely from current upstream documentation. Private E10 retains source, image, configuration hash and storage details.

Replication of the metadata volume is not replication of the NAS object blocks, and neither is independent backup. Immediate PUT/HEAD/GET demonstrates readable bytes, not survival of process, guest, NAS, host or power failure. An Application marked Healthy likewise proves none of those loss boundaries. No failure, write, deletion or throughput test ran during E10.

## Selected storage and accounting

Use one private Weather bucket on the admitted capacity-oriented tier for all required object classes, including database archive. Dense snapshot staging remains the separate D03 grant. Do not select the small availability-oriented tier just because its API is healthy or silently split the canonical bucket to avoid D08. Bucket name, quota, credentials and namespace boundaries are owner-provisioned later, not copied from another tenant.

Admission must include every rendition/poster/sidecar, keogram/map, private hourly original, public comparison image, preview churn, retained metadata revision, database base/WAL/history, recovery manifest, orphan grace and multipart residue. Calculate retained growth and transient peak separately; five-year originals and video retention are distinct policies. E06's sample JPEG arithmetic cannot substitute for measured p95 camera or encoded-output sizes. A PVC capacity field is not free NAS capacity, a reservation or permission to consume it. Preserve shared NAS load/watchdog limits and recovery headroom.

Maintain per-class used/pending/pinned bytes and count independently of bucket-level totals. Apply admission/backpressure before exhausting the grant. Keep failed/partial products explicit; storage pressure cannot authorize deleting the last source required by a pending hourly original or keogram. Preview rolling history remains bounded and does not become a permanent camera archive.

## Capacity envelope and sample-based scenarios

Use a per-class ledger with logical retained bytes/count, transient pending/pinned bytes, physical replication/overhead, and offsite retained versions as separate columns. Required admission cannot be derived from a single bucket/PVC size. [Exact sample arithmetic](../research/capacity-scenarios.json) uses E06's one successful JPEG per camera; it is neither a p95 estimate nor an upper bound.

| Scenario at those sample sizes | Logical payload only |
|---|---:|
| Combined dense capture at 1 Hz | 6,971,099 bytes/s |
| One day of dense writes | 602,302,953,600 bytes (602.30 decimal GB) |
| 48-hour dense backlog | 1,204,605,907,200 bytes (1.20 decimal TB) |
| Same backlog with 30% margin, before scratch | 1,565,987,679,360 bytes (1.57 decimal TB) |
| Hourly originals, one 365-day year | 26,280 objects / 61,066,827,240 bytes |
| Hourly originals, five 365-day years | 131,400 objects / 305,334,136,200 bytes |

The original requirements' 337 GB staging example assumes much smaller JPEGs and is not a valid Weather allocation based on E06. Real staging admission retains §6.8's `1.3 * (sum(camera_p95_bytes / interval_seconds) * maximum_unprocessed_seconds + maximum_render_scratch_bytes)` and adds separately measured active/sealed metadata, compact-promotion overlap, preview work, quarantine and deletion lag where not already counted. Count each physical file once; a second pin is not a second copy, while a verified compact extraction coexisting with dense input is. The 48-hour scenario is illustrative, not the selected backlog grant. Do not silently shorten retained evidence or lower cadence to fit it.

Garage admission must separately calculate:

- Six camera/segment video products per complete day, each with required native/web/mobile renditions: 18 rendition objects before posters, sidecars, maps and corrected revisions. Use measured per-rendition sizes/durations and their actual retention horizon, not JPEG size or a guessed bitrate.
- One retained native original per actual elapsed local hour/camera plus its required sanitized comparison variants. Count UTC slots across the selected civil-year retention interval, including leap years and 23/25-hour days; the five-365-day table is only arithmetic context.
- Both segment keograms/maps for every camera, and preview generation at the selected one-minute schedule. A full 24-hour horizon can contain up to 4,320 per-camera preview generations before derivative multiplicity; failed/missing capture is not guaranteed capacity relief. Use measured derivative sizes and account for pending/orphan revisions independently.
- Database data/index growth separately from archived base/WAL/history; base retention and WAL generation/outage catch-up determine archive occupancy. Include verification shards/manifests, durable metadata revisions and multipart/orphan grace. No model output estimate substitutes for a DB/WAL measurement.

For each physical tier, multiply by its actual replication/storage overhead rather than counting the metadata volume's replicas as object replicas. Offsite accounting uses provider versions and their protection/hide lifecycle, not only currently visible Garage keys: a 24-hour source preview horizon does not imply 24-hour B2 occupancy. D15 must budget copy and full-content verification I/O, initial seed, restart/catch-up and required recovery-held history. Compare aggregate admitted daily changes against the shared transfer window after other tenants and gates; its theoretical ceiling is not Weather's allocation.

OWN-NFS/OBJECT/DB/CUSTODY supply measured inputs, free capacity/reservations and accepted loss/backlog objectives; Weather records the resulting versioned per-class envelope. Until those inputs exist, capacity remains unqualified. These calculations request no share scan, storage change or throughput experiment.

## Receipt levels and publication

Separate these facts rather than overloading one uploaded flag:

| Receipt | Required proof | What it does not prove |
|---|---|---|
| Object content verified | Immutable identity, exact object size/SHA-256, complete operation result and qualified readback through D08 | Crash/power survival, offsite copy or catalog publication |
| Local durability qualified | Storage-owner policy/version and receipt applicable to this object acknowledgement, installed build/configuration and accepted failure/loss boundary | Independent restore, additional copies or uninterrupted playback |
| Catalog publication committed | Verified references plus generation CAS/FKs/outbox in one CNPG transaction | Object and DB form one distributed transaction |
| Public readback verified | Same-origin class/visibility/metadata/byte behavior for the committed revision | Backend power-loss safety or all supported browsers |
| Independent custody verified | Exact offsite object version/hash/protection and D15 checkpoint closure | That every later object is already protected or that a current preview should be restored |

The local durability receipt is an owner-backed qualification bound to the current storage path and object operation, not an application-invented success document. It must identify the policy/receipt revision, exact storage build/configuration identity, object identity/hash, acknowledgement/verification timestamps and the accepted loss/failure scope. If the effective path changes or qualification expires, stop granting new cleanup eligibility until requalified. Retain historic evidence rather than rewriting old receipts to the new policy.

Use the established immutable upload → verification → catalog CAS/outbox → same-origin readback protocol. Uncertain multipart completion or a lost response must be reconciled by exact immutable identity; an HTTP 200 prefix is not sufficient. Same-key concurrent writes and Barman compatibility remain D08 obligations. Catalog publication may be useful before independent custody completes, but cannot be reported as offsite recoverable until the corresponding receipt exists.

## Raw cleanup and independent lifecycle

Raw cleanup requires applicable local durability acceptance for every required rendered artifact, committed publication/readback, and no remaining source-dependent pins. Hourly originals must be durably archived with FKs or transferred with their obligations into the qualified compact working set. Keograms need durable outputs or protected compact inputs. AI/search use completed media and do not retain dense captures.

Central B2 custody is asynchronous and is not automatically added as a prerequisite for every raw deletion. However, no accepted local-loss window or qualified durability mechanism exists merely because the central job will eventually copy an object. OWN-OBJECT must resolve the actual local guarantee or a clearly scoped alternative before cleanup is enabled. A different synchronous-copy requirement would change retention/capacity/latency and must be recorded explicitly, not silently treated as the original design.

Object retention uses current references, source/promotion/recovery pins, historical/PITR dependencies, tombstone grace and role authorization before each deletion. Do not install a bucket-wide expiry policy that ignores those relationships. Expiring a video never cascades into a still retained under the separate hourly policy. Barman retention must preserve the selected usable base/WAL chain; recovery receipts and reused predecessors remain pinned under D15.

## Qualification and rollback

OWN-OBJECT must resolve actual custom-runtime fsync/SQLite acknowledgement behavior, NFS/NAS stable-write semantics, required owner remediation and the accepted measurable loss boundary. Scope failure qualification to an admitted disposable instance/storage path; do not power-cycle production to obtain proof. Include process restart, uncertain write/commit, storage interruption, recovery of metadata and blocks, corrupt/missing object detection and evidence that no cleanup predicate overclaims the result. A finite test set supports only its tested failure model, not arbitrary zero-loss certification.

WX-06 verifies grant/quota/private bucket and applicable durability receipts. WX-17/18 verify upload/catalog failures and pin-safe cleanup. WX-28/30/32 prove archive retention, common closure and independent restore. Preserve object inventories, immutable references and policy history on rollback; stop new uploads/cleanup as needed and restore compatible application/provider configuration through the owner. No rollback deletes retained data or weakens the access boundary.

D07 remains open until capacity, actual acknowledgement-to-stable-storage behavior and the accepted local-loss window are resolved. E10 strengthens the evidence for this gap; it does not qualify the current tier for Weather raw cleanup.
