# D05 — receipt durability, canonical measurements and coverage

Status: **protocol selected; storage/capacity admission and observed cadence qualification pending**. Implements requirements §§9.1–9.4, 10.1–10.7 through WX-08/11/12/13/40/41/42. [D04](D04-tempest-network.md) selects two independent receivers; [D06 in the register](implementation-register.md) owns database and spool storage admission. This is a design, not an implemented collector or evidence of loss-free reception.

## Decision and alternatives

Keep an immutable private receipt ledger, versioned parser projections and separately revisioned canonical measurements. Never delete duplicate receipts to save space. Each receiver has its own retained, single-writer spool on admitted block storage independent of snapshot NFS; no Kafka or other streaming service is required. Database unavailability must not block socket draining until the bounded spool/queue capacity is exhausted.

Reject direct-to-database-only collection because a database interruption would lose receipts immediately. Reject snapshot-NFS buffering because it couples otherwise independent collection to camera storage. Reject payload-hash-only event deduplication and receive-time freshness because each can silently change measured history. Two listeners improve opportunities to receive; they do not prove transport completeness.

The [official v171 protocol](https://weatherflow.github.io/Tempest/api/udp/v171/), reviewed 2026-10-23, identifies observation arrays, event timestamps and diagnostic fields. In particular, precipitation is described over the preceding minute, while wind averages use the report interval. Event packets have no unique strike identifier. These are protocol inputs to this design, not proof of this station's current firmware behavior. E03 only observed headers; actual families, interval behavior and clock quality remain unmeasured.

## Receipt and spool transaction

Use `(receiver_id, incarnation_id, sequence)` as receipt identity. The persistent receiver ID identifies the admitted spool owner. Generate a new random incarnation before each writer start, persist it before accepting data, and increment a nonnegative sequence within that incarnation. Old incarnations remain replayable. Never reuse an incarnation after an uncertain restart. Bind monotonic time to the incarnation; monotonic values from different incarnations are not comparable.

Receive exact bytes using a full IPv4 UDP-sized buffer, retaining actual byte length, interface/destination metadata, source endpoint, wall-clock UTC, monotonic counter and kernel overflow observations. Truncation is an integrity failure, not a valid receipt of the whole datagram. Enforce bounded parser nesting/array/string work after raw persistence, not by truncating a received payload. Denied sources are counted by the network policy; this ledger covers allowed-source datagrams delivered to the service.

Append framed records containing identity, metadata, exact bytes, SHA-256 and a record checksum to an append-only local segment. A record becomes `durably_spooled` only after successful file synchronization; synchronize directory changes when creating/rotating segments. Select per-record synchronization initially, with bounded socket/queue draining separated from the writer. Group commit is a future measured optimization requiring an explicit volatile-loss budget. Storage flush honesty and failover fencing still require platform qualification; fsync alone is not a physical-loss proof.

On startup, scan bounded segments, verify framing/checksums and quarantine a torn final record. Interior corruption quarantines the affected segment and records an integrity incident; do not skip unknown bytes and silently resume. Never fabricate missing payloads. Recovered complete records retain their original identity and receive times. An unpersisted process tail can be lost; counters held only in that process cannot establish its exact length after a crash.

Submit bounded database batches. In one transaction insert the immutable receipt and durable projection-work/outbox record. An existing identity with identical bytes and immutable metadata is an idempotent retry; a mismatch is an integrity conflict and must not overwrite either evidence set. A small non-time-partitioned identity registry enforces uniqueness across time partitions; partition creation or a parser changing its timestamp cannot bypass receipt identity. Retain that registry while its receipt remains in retained history. No automatic detailed-history expiry is selected.

Delete/compact a spool segment only after every contained receipt has a durable commit acknowledgement and a synchronized acknowledgement checkpoint. An uncertain commit is retried using the same identity. DB commit acknowledgement must meet the admitted D06 write policy; receipt cleanup is not a claim of independent backup completion. Projection failure cannot erase raw history or prevent other receipts from committing.

The spool volume has one fenced writer, not two processes trusting a lease expiry. During relocation, wait for storage/node fencing before mounting read-write; retain unavailable volumes for reconciliation. The other receiver continues with a different ledger. Rollback stops new readers, reconciles acknowledged records and retains uncommitted segments; it never empties a spool to make a deployment healthy.

## Capacity and loss accounting

Admission must set finite values for socket bytes, reader queue bytes, maximum record bytes, segment bytes, total spool bytes, reserved diagnostic space, transaction batch bytes and replay rate. Let measured peak persisted bytes/second be `p`, admitted outage duration be `T`, and measured burst/recovery reserve be `b`: required usable capacity is at least `p*T+b`. Count both receivers in database/WAL/backup estimates. A second receiver duplicates raw custody demand even when canonical aggregates deduplicate.

Use operational warning at 70% and critical at 85% of admitted usable spool capacity. At the hard data capacity boundary, retain existing unacknowledged records; do not overwrite oldest history. Reserve diagnostic space separately. Continue bounded receive/drop accounting where feasible, and expose `capacity_loss` plus its observed interval/count. If diagnostic writes also fail, mark loss magnitude unknown. Socket overflow, application queue drops, spool write failures and capacity drops are separate signals; a counter discontinuity/restart is not zero loss. UDP loss before the socket is unobservable without independent source evidence.

Replay gets a bounded DB budget while fresh receipts retain reserved ingress capacity. Measure replay throughput greater than sustained ingress before promising recovery within a target. If backlog cannot drain, expose degraded capacity and revise admission; do not invent an unlimited outage guarantee. Numeric byte budgets and outage duration remain a D05/D06 admission deliverable, not guessed production defaults.

## Parser contract

Preserve raw bytes for malformed UTF-8/JSON, duplicate object keys, unknown types, invalid lengths and unknown extra fields. Duplicate JSON keys are quarantined for normalization rather than silently taking the last value. Store a parsed representation when safe, but never use it as a byte-for-byte substitute. A parser result is keyed by receipt identity, parser version and row ordinal. Process every row independently; a malformed row does not discard valid siblings. Unknown fields/rows stay available for a future parser revision.

Preserve reported units and values alongside canonical units. Null, absent, invalid and measured zero are distinct. Validate metrics individually, applying sensor-health and versioned range/sentinel rules. Do not infer that an invalid temperature invalidates an otherwise valid wind value. Link device diagnostics by source time and explicit bounded relevance; stale diagnostics are unknown, not permanent proof of health/failure. Raw diagnostic bitfields remain intact even where only known bits are interpreted.

## Canonical observation and event identity

Observation candidate key: configured private station/device identity, family and source timestamp. Every row contributes a variant containing its complete reported row and relevant interpretation metadata. Matching variants across receivers/receipts reference one canonical observation. Preserve all receipt-to-variant links. Hashes accelerate lookup but byte/value comparison confirms equality; different raw JSON whitespace need not create a different measurement.

Different values at the same candidate key are a conflict, not a last-arrival-wins update. Exclude disputed metric values from qualified aggregates until a versioned reconciliation selects evidence or marks them unavailable. Uncontested fields may remain usable with the conflict recorded. Duplicate rows within one packet remain distinct raw projections but cannot create extra support duration or double accumulation. Do not invent a reboot epoch from arrival order; late pre-reboot traffic must not become a new observation just because a status reset arrived first. Clock resets/collisions preserve uncertainty.

Use interval lightning counts from the configured authoritative observation family/device for measured totals. Never add `evt_strike` packets to those totals, and do not sum legacy and Tempest observations of the same phenomenon. Store event receipt clusters for timeline annotations using source time and full event parameters; label multiplicity ambiguous when repeated identical same-second events cannot be distinguished from retransmission/dual reception. Do not claim one cluster proves exactly one physical strike. If interval counts are absent, measured count coverage is unavailable; the feed may still say a strike was reported. No silent event-count fallback changes the counting authority.

`evt_precip` reports onset, not accumulated amount. Repeated reports can link to one onset annotation without adding rain. Event clustering, canonical selection and reconciliation are revisioned with evidence links, never destructive raw deduplication. Authoritative device/family changes are effective-dated configuration revisions and may not overlap for a single total.

## Source time, support and revisions

Source time governs current values and aggregation. Receive time only describes transport delay/clock quality. Future/impossible timestamps remain in raw history but are ineligible for current display until validated under a declared clock-tolerance policy. Replaying old receipts cannot extend expiry. Replace a current metric only with a valid later source observation or an explicit correction revision for the same source time; never with a newer receive time alone.

For instantaneous/state metrics, use a forward hold bounded by the next valid sample and the admitted per-metric maximum hold. Do not extend through a known invalid-state interval. Min/max are observed values; duration-weighted means use only supported duration. Support and freshness policies are effective-dated and versioned; the UI's 15-second refresh is not a sampling or validity interval.

For interval measurements, persist explicit support start/end and interpretation provenance. Do not expand the preceding-minute precipitation support merely because the packet report interval increased. If actual firmware support is unresolved, retain amount with unknown support and exclude it from qualified interval totals until resolved. Distinct overlapping accumulation reports require reconciliation; clipping each independently can double count. Allocate a qualified boundary-straddling amount by duration overlap only as a labeled uniform-intensity estimate, conserving its total across adjacent windows.

Lightning counts stay integers. A boundary-straddling count belongs to an explicit boundary-uncertain record; show the fully contained observed subtotal separately, without fractional allocation or duplicating the count into both qualified totals. Annotations may link both windows without counting twice. Solar integration, circular wind direction, pressure labeling and observed accumulation follow requirements §10.5; no missing-period extrapolation is implicit.

For metric `m` and half-open window `W`, `coverage_m = duration(union(valid_support_m) intersect W) / duration(W)`. Union overlapping support before computing duration. A count of samples divided by expected samples is a separate transport diagnostic, never this coverage fraction. Exact elapsed durations apply on 23/25-hour local days. Conflicts, unsupported intervals and invalid fields reduce that metric's coverage.

Canonical changes atomically create a new evidence revision and enqueue affected aggregate, event, almanac and search revisions through the existing durable outbox/CAS contract. Update dependent AI synthesis asynchronously; measurement availability does not depend on inference. Keep prior evidence revisions and visibly invalidate stale dependent claims until recomputation completes.

## Acceptance and remaining qualification

The [adversarial vectors](../contracts/telemetry-vectors.json) define synthetic expected behavior for implementation tests. They are specification inputs, not passing parser tests. WX-11 owns receive/spool crashes and loss accounting; WX-12 owns full-family projection and deduplication; WX-13 owns support/freshness/formulas; WX-40/41/42 own event/almanac/search revision propagation. WX-08 enforces schema/identity constraints.

Before closure, collect private full-payload evidence with authorization and admitted storage, measure family/cadence/clock/power-mode behavior, select byte/hold/clock budgets, and qualify both receiver spools, source conflicts, fsync/fencing, DB acknowledgement and recovery throughput. Public fixtures contain synthetic identities only. All raw provenance, source endpoints and receipt routes require private operator authorization; none are public API output.
