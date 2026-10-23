# Telemetry and database-history audit

The [source-bound ledger](history-clause-audit.json) preserves every prose/table block in §§9.1–9.4 and 10.1–10.7. It retains the complete packet-family and conceptual-entity tables rather than reducing the obligation to selected public metrics. Concrete producers below supply implementation evidence; WX-33 integrates it. Mapping is not acceptance.

## §9.1 — WX-04, WX-11

- Direct local UDP 50222 is primary; reference ESP32 omissions/retired DB are not production templates, and code imports need actual license review.
- Qualify actual broadcast VLAN/interface, existing receivers, reachability/port/firewall and selected attachment/relay; hostNetwork/ClusterIP/LoadBalancer does not magically receive VLAN broadcast.
- Separate camera capture, filter source/device without claiming cryptographic authentication, keep listener private and prove delivery/dedup before promising receiver redundancy.

## §9.2 — WX-11, WX-12

- All allowed-source received datagrams are in scope, not a promise against pre-arrival UDP loss; distinguish received/spooled/committed/projected/quarantined/capacity-lost.
- Preserve exact bytes plus valid parsed JSON, stable receipt/provenance/hash/parser state and unknown fields; process every observation row.
- Retain obs_st complete fields, every rapid_wind sample, event parameters, full status diagnostics, legacy obs_air/obs_sky and unknown/malformed bounded bytes.
- Protocol field order/units/null/sentinels/report intervals and raw-versus-derived values are versioned fixtures; cadence/firmware/power modes affect freshness independently.

## §9.3 — WX-11, WX-12

- Stable receive identity precedes DB submission; uncertain commit retries same ID. Separate canonical observation identity across receivers.
- Do not universally deduplicate by payload hash or timestamp; preserve legitimate equal events/diagnostics, conflicts and provenance.
- Durable bounded independent spool, qualified writer transfer, delete only after durable DB commit, no new streaming platform by default.
- Source time controls freshness, replay never updates old readings to current; flag future/clock/reset, retain raw and revise history without replacing newer current values.
- Measure socket/queues/flush/recovery bursts; explicit warning/stop/loss budget; slow DB cannot block receiving or trigger unbounded buffering/HA-cloud substitution.

## §9.4 — WX-11, WX-12, WX-31

- Nominal 3-second/minute rates and 1-kB growth are arithmetic examples; measure actual receipts/rows/index/WAL/replication/backup growth.
- Separate arrival gaps, spool loss, parse/project failure, reduced device cadence, backlog and station outage.
- Validity/freshness is per field/metric; pressure/temperature cannot inherit rapid-wind cadence.

## §10.1 — WX-05

- CNPG existing operator with dedicated Weather isolation selected by D06; shared alternative must resolve whole-cluster physical backup/other-tenant scope.
- Qualified three-instance placement across physical domains, anti-affinity/disruption/readiness/reservations and synchronous availability/loss semantics.
- Postgres and storage replicas are different; no excluded R1 grant or blind replication multiplication.
- DB storage independent of snapshot NAS; separate WAL archive-outage budget; API placement and NAS media/capture failure domains remain distinct.
- Pin compatible PG/CNPG/vector/extension images and test upgrade/failover/restore together; healthy operator does not prove extensions.

## §10.2 — WX-05, WX-08, WX-12

- Choose native partitioning versus Timescale based on measured scale/queries/retention/image/license/recovery; no confusion between operator and extension.
- Complete accepted detailed/raw history is retained; obsolete 90-day policy and automatic sole-record retention drops forbidden; rollups supplement.
- Capacity expansion/archive strategy must keep retained fields queryable.
- Partition source/receive data with late/future/default/missing-partition handling; preserve supported receipt uniqueness through registry or partition-aware key rather than unsupported global partition key.

## §10.3 — WX-08

- Represent every conceptual entity in the source table with explicit UTC/units/versioned bounded metadata and enforceable identities, not merely JSON strings.
- Separate station raw events from annotations, solar cycles from calendar days/hours, raw capture from private originals/public derivatives and automated from human revisions.
- Keep large JPEG/video bytes out of CNPG; source snapshots only admitted NFS, selected private originals Garage, metadata/history CNPG.
- Separate ingest/orchestration/analysis/public/editor/migration/backup roles; public parameters never choose raw tables.
- Enforce capture/camera/day/hour/source and asset-class relationships; no shorter-lived-video cascades to stills/events/days.
- Retain canonical FK/tombstone references across retries/corrections/restore and prove no active orphans; sidecars export but cannot replace DB constraints.

## §10.4 — WX-13

- Versioned same-origin measured current/history APIs cover available weather metrics and quality, never AI substitutes.
- Per-metric source/receive time, cadence/quality/expiry; persist last-known and age cached responses during DB/restart failure without fake zeros/freshness.
- 15-second browser refresh and candidate 5-second backend cache do not reduce native receipt preservation; no separate rapid-wind UI or HA/cloud runtime fallback.
- Typed metrics/time/resolution/aggregate/cursor bounds, raw interval/page/point limits and suggested aggregate resolution without changing meaning.
- Complete cursor-paged history exports and authenticated raw/diagnostic field access; protect identifiers, expose public aliases.
- UTC/unit schemas, null-with-reason, deterministic conversion and Denver rain-day bounds; admitted SQL/pool/rate/cache/export budgets must protect ingestion.

## §10.5 — WX-13

- Exact half-open video interval and independent metric union-support coverage, bounded holds and deduplicated/versioned inputs.
- Observed temperature/humidity extrema and time-weighted means; precipitation unique-support conservation with labeled uniform overlap assumption.
- Duration speed, reported gust maximum and circular/vector direction with calm/weak-resultant semantics; pressure station label and qualified endpoint trend.
- Solar valid-second integration /3600; one integer lightning counting authority; instrument precipitation class separate from visual interpretation/snow depth.
- No sample-count coverage, missing-period extrapolation or full-period dry conclusion from sparse zero.
- Stable metric/value/unit/window/algorithm/coverage evidence IDs; late/parser corrections revise aggregates and dependent metadata with provenance/history preserved.

## §10.6 — WX-41

- Observed daily/monthly/yearly records, rain calendar, wind rose and historical comparisons plus pinned local sun/moon derivation; no remote feeds or coordinate leak.
- Real calendar-day duration, units/support/formula/library/coverage/history/revision; conserve observed rain with incomplete labeling.
- Duration-weighted wind sectors/speed bins and separate calm/variable/missing with visible denominator; no arithmetic compass average or duplicate-time counts.
- Available-history records are not climate normals; minimum coverage policy qualifies/excludes inadequate ranking and unequal-duration/missing-day comparisons.
- Recompute affected records on correction; preserve bounded queries/exports without implying new CSV, degree-day or gardening scope.

## §10.7 — WX-40, WX-43

- First-class measured/visual/manual annotations separate from raw receipts; interval/precision/evidence/rule-or-author/threshold-units/camera/day/visibility retained.
- Station-wide navigation links by overlap without claiming all cameras visibly show it; one identity across midnight and civil boundaries.
- Clip findings retain interval precision; missing footage permits weather-only event; lightning report not proof of visible flash.
- Durable revision/outbox updates, measured events independent of AI, validated visual events asynchronous.
- No additional clip, raw-retention extension or outgoing notification; rule threshold/hysteresis/dedup plus repeated/late/overlapping/withdrawal/feed-load acceptance required.

## Evidence, design and unresolved qualification

D04/E03 proves passive station broadcasts on candidate trunks, not isolated pod reception or packet completeness. D05 defines raw/canonical receipt semantics with declarative vectors, not a running parser. D06/E05 proves live PG/vector availability and observed synchronous settings on other consumers, not Weather admission. Existing database migration and API contracts cover constraints/projections; aggregate and event contracts resolve additional formula/identity choices. The current API shape receipt is not execution evidence for these requirements.

Material gaps remain: admitted secondary network/isolation, full private payload/cadence/reset evidence, independent spool grant/flush/loss budget, Weather database/storage/archive admission, complete raw/normalized query mappings, per-metric hold/clock/pressure policies and actual load/resource budgets. Preserve original received bytes and complete-history scope while resolving them. Do not interpret an unknown projector, oversized query or unavailable model as permission to delete or hide retained fields.

Cross-check final migration design against every row of the retained conceptual-entity table and every original protocol field, not only the public DTOs. Verify replay and cross-record semantics separately from JSON Schema. Actual receive/spool/commit/project counters, partition/failover behavior, public/private queries, formula tests and restored-history acceptance remain unproven. No ingest/database or live telemetry configuration changed during this audit.
