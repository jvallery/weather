# D05 — receipt durability, canonical measurements and coverage

Status: selected under [P4](2026-10-24-simplification.md). Source implementation is WX-11 (#12); database integration and live commissioning remain separate acceptance gates. This decision replaces the earlier dual receiver, custom framed WAL, variant registry and fencing design. Historical text is available in Git.

## Custody and admission

One `tempest-receiver` process receives UDP 50222 on the commissioned Linux pod. Its socket uses `IP_PKTINFO`, a 65,535-byte payload buffer and ancillary truncation checks. Wrong interface, wrong configured hub source, parseable wrong hub/device identity, or truncation is dropped and counted. Protocol v171's `obs_st` envelope does not require `hub_sn`; the configured source and device identity must match, and a supplied hub identity must match too. Hub status requires the configured hub identity.

Malformed UTF-8, malformed JSON and duplicate JSON keys from the configured interface/source retain their exact bytes as quarantined receipts. Their identity is unknown: no identity is guessed from malformed bytes, and they never become normalized/public observations. Unknown parseable families require a configured identity and remain queryable as private raw/parsed receipts.

The receive loop never waits for CNPG. It performs one SQLite WAL transaction with `synchronous=FULL` per accepted datagram, before field normalization. Each row retains a random receipt UUID, original receive UTC nanoseconds, monotonic nanoseconds bound to a receiver incarnation, interface/source/port, length, SHA-256 and exact bytes. Distinct UDP deliveries get distinct UUIDs even when their bytes match. Retrying one durable row keeps its UUID and all original metadata.

The spool has a single local owner lock on its RWO volume; this is not cross-receiver fencing. SQLite committed rows are the receipt ledger. An uncommitted transaction after a crash is not a fabricated receipt or recoverable custom-WAL sequence. Restart records unknown volatile loss; it cannot establish how many datagrams were in an unpersisted process/socket tail. Unreadable/corrupt generations are moved aside intact, including SQLite sidecars, with an unknown-loss marker. They are never discarded to make the service healthy.

A bounded drain transaction inserts raw receipts, row provenance and normalized observations together. After the database transaction's commit returns, SQLite deletes acknowledged rows in a FULL transaction. An uncertain database acknowledgement leaves rows pending for replay. A crash after the CNPG commit and before local deletion replays the same UUID; no second raw row or projection is created. Database acknowledgement does not establish independent backup completion.

A receipt UUID with changed bytes/hash or immutable metadata creates an integrity incident containing the alternate bytes and metadata. The original receipt is unchanged; the alternate is retained as private evidence and never silently normalized as the original.

## Bounds and loss

The default logical capacity is 1 GiB, with diagnostic/transaction headroom reserved inside it. The physical accounting includes retained unreadable generations and SQLite sidecars. The effective data limit can be reached earlier than the PVC size. Warning begins at 70% and critical at 85% of admitted data capacity. At the limit, each new datagram increments durable `capacity_loss`; unacknowledged rows are never overwritten. If diagnostic storage itself fails, loss magnitude is unknown. Cumulative diagnostic snapshots reach CNPG idempotently; use the latest/max cumulative count per spool generation and reason, never sum snapshots.

The drain is bounded to 128 receipts/1 MiB per batch and a 30-second transaction timeout. There is one receiver and one drain, with no unbounded memory queue. Kernel overflow, truncation, capacity loss, uncertain spool writes and drain errors have distinct metrics. UDP loss before delivery to the socket remains unobservable without independent source evidence. Throughput, physical flush honesty, node/PVC recovery and sufficient outage capacity require commissioning evidence; defaults are not measured guarantees.

## Parser and canonical rows

The field mapping follows the [official WeatherFlow UDP v171 protocol](https://weatherflow.github.io/Tempest/api/udp/v171/). All eight families are parsed: `obs_st`, `rapid_wind`, `evt_precip`, `evt_strike`, `device_status`, `hub_status`, `obs_air`, `obs_sky`. Every `obs` row keeps its ordinal; invalid siblings receive private provenance/quarantine without discarding valid rows. Extra envelope/row fields and known/unknown diagnostic bits are retained privately.

Validity is per field. Null, absent, invalid and measured zero are distinct. Canonical numeric values use protocol names, with reported/canonical units and explicit range policy alongside private reported values. Station pressure remains station pressure (`mb` equals `hPa`); no sea-level correction is invented. Units omitted by v171 remain unknown, including strike energy and legacy AIR battery. Firmware/status values do not become public weather metrics.

PostgreSQL JSONB cannot represent every legal JSON value. Exact raw bytes remain authoritative. An incompatible parsed envelope is SQL NULL; incompatible private evidence leaves carry an explicit `_weather_exact_private_json_text` representation. No lossy numeric/string coercion supplies a canonical measurement. Timestamp precision beyond a PostgreSQL microsecond remains private/quarantined rather than silently collapsing canonical keys.

The canonical key is `(device_id, message_type, source_ts, row_idx)`. `ON CONFLICT` preserves the first canonical values and first receipt. Every receipt/row links to that canonical row. Differences under the same key create lean conflict metadata linked to both receipts; disputed fields become ineligible with `payload_conflict`, while uncontested fields may remain usable. No last-arrival-wins value update, variant registry, arrival-derived epoch or dual-receiver reconciliation is introduced.

`evt_strike` is a reported annotation and never adds to measured interval lightning counts. Repeated same-second events under one canonical key have ambiguous multiplicity; no unique physical strike count is claimed. Only configured `obs_st`/`obs_air` interval counts can be measured count authority. `evt_precip` indicates onset only and never supplies accumulated rain.

## Source time, diagnostics and consumers

Source time governs weather freshness, current projection and aggregates. Original receive time describes transport and is unchanged by replay. Rows beyond the configured future tolerance (default 120 seconds) remain stored but ineligible. Device validity dates qualify source timestamps. A clock reset or late row never creates a new epoch from arrival order or replaces newer current weather.

Transport diagnostics compare original receipt times with reported cadence and explicit device state. They retain arrival gaps, capacity loss, parse failure, reduced cadence, DB backlog and station unavailability as separate reasons. A slower reported cadence is not counted as loss. Monotonic and UTC deltas detect receiver clock discontinuity; a clock jump cannot fabricate a transport gap. Per-family UTC minute counts/cadence and diagnostic interval bounds are private operational evidence.

Transport receipt counts are not meteorological support coverage. Per-field quality exposes `eligible`, fixed reasons, reported/canonical units and explicit support policy/provenance to WX-13 (#14). `obs_st` rain has a preceding-minute protocol candidate but remains firmware-unqualified; legacy rain support is unknown. Report interval never expands rain support. Wind report-interval support has an explicit bounded interpretation policy. Instantaneous holds, status relevance, physical range qualification and effective-dated support policies belong to #14/commissioning.

For metric `m` and half-open window `W`, `coverage_m = duration(union(valid_support_m) intersect W) / duration(W)`. Unknown/invalid/conflicting support reduces that metric's coverage. See the [aggregation contract](../contracts/weather-aggregation-contract.md) for duration weighting, conserved rain allocation, circular direction and integer lightning boundaries.

The narrowly scoped conflict function updates only disputed `quality.fields` and emits `telemetry.conflict` with opaque incident/observation IDs, family, source time and official field names. Canonical values, receipt and source time remain unchanged. #14 invalidates only the source-matched current sample; subsequent outbox consumers invalidate/recompute dependent products without blocking measurements on inference.

## Acceptance

[Telemetry vectors](../contracts/telemetry-vectors.json) are test specifications, not passing evidence. T01 is one datagram delivered twice; T16 describes SQLite commit boundaries, intact unreadable-generation retention and truthful unknown volatile loss. Pure parser/admission/spool checks can run locally. Real PostgreSQL, abrupt-exit and database outage tests run only in the approved fleet CI image. Live reception, cadence/firmware qualification, NAD/PVC/NetworkPolicy and recovery capacity are WX-04 (#5). No raw bytes, source addresses, serials or private diagnostics appear in public API output or operational logs.
