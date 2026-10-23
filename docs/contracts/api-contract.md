# HTTP and schema contract

Status: machine-readable design draft for Phase 1; not deployed and not yet a completed full-contract audit. [OpenAPI](openapi.json) describes 16 paths; [JSON Schema](api.schema.json) defines public and private operator representations. Schemas describe allowed shapes, not authorization. Publicly publishing a private-response schema does not authorize serving its data anonymously.

Use [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html) and [JSON Schema 2020-12](https://json-schema.org/draft/2020-12/json-schema-core), reviewed 2026-10-23. Validation must enable date/time format assertion and resolve references locally; production must not fetch schema URLs on requests. Schema URLs identify versions, not required runtime network dependencies.

Consumers: WX-08/13/17/19/20/24/25/26/36/37/38/39/40/41/42/43/44. The private [capture/index and seal contract](capture-contract.md) and [recovery envelopes](recovery-contract.md) are separate from public serializers. Deployment configuration, private hourly archival receipts and the central owner's detailed recovery-ledger integration remain outstanding Phase-1 artifacts. The private candidate [visual-model schema](visual-analysis.schema.json) implements §13.5's shape; its semantic checks are specified below.

## Effective editorial content and analysis coverage

`EditorialPublic.description` is the effective, currently eligible description, whether generated or human-edited; null means no eligible description. It is separate from immutable automated claims. `human_edited` and the effective revision label its provenance. An invalidated evidence binding cannot remain visible merely because this string still validates. Public sidecars, UI and lexical/semantic indexing must consume the same effective revision. Private notes and favorites remain excluded. The 8,000-character interface bound is a protective ceiling, not permission to bypass grounding.

`Analysis.coverage` separately describes supported reviewed source duration, with gaps and algorithm identity. Capture coverage does not establish analysis coverage. Union reviewed, valid source windows; do not count repeated/overlapping clips twice. Pending/unavailable work has zero supported duration, while actual unassessable media remains explicitly limited; do not claim full visual evidence solely because the request included the entire video. The coverage algorithm must define its assessability policy and preserve per-claim limitations.

## Private visual-model candidate boundary

[visual-analysis.schema.json](visual-analysis.schema.json) enforces the exact category/assessment/confidence vocabulary, at most 16 observations, eight limitations and 240 characters per description/limitation. Evidence lists are nonempty, unique and bounded at 256 opaque IDs. This last ceiling is a selected interface protection; D11 must keep actual per-request evidence below it.

Schema-valid model text is still untrusted. Check all evidence IDs against the request's supplied set, each observation's IDs against the result's declared set, and the supplied IDs against the current media/evidence revision. Rebind candidate observation IDs to deterministic application IDs. Require explicit precipitation and visibility assessments when assessable, and an honest not-assessable result when night/obstruction prevents assessment. Reject invented IDs, unsupported claims, prompt-injected instructions, truncation and stale jobs before publishing. No numeric confidence percentage replaces the categorical field. The schema is private candidate input; it is not the public Analysis representation and must never be returned raw to browsers.

The [visual fixtures](visual-analysis-fixtures.json) exercise shape limits only. Semantic fixtures must include a schema-valid invented evidence ID, omitted assessable precipitation/visibility, repeated generated IDs across clips, scene prompt injection, stale revision and forward/reverse temporal controls. Passing the shape suite does not close native-video qualification or grounding acceptance.

## Routes and compatibility

All data routes use /api/v1; media/download paths are same-origin opaque-ID paths. Search uses POST for its bounded structured read request; shareable UI URLs serialize the same typed filters without implying a mutating operation. Basic catalog links remain usable without JavaScript.

Unknown properties are rejected for serialized objects and editorial/search input. Additive optional fields require a schema-version compatibility decision for strict consumers; do not silently expand v1 payloads and assume all generated clients tolerate them. Media and metadata revisions remain distinct from schema version. Errors use a bounded code/message/request ID with no stack, SQL, device identity, bucket key or model payload.

A public ID is not a capability. The media resolver checks class, committed publication, trusted exposure eligibility, expiry and tombstone before returning headers or bytes. It cannot accept arbitrary upstream URLs/keys. IDs in operator routes similarly require subject-specific authorization.

The proposed session/CSRF names describe the same-origin interface. D13 must bind this to the approved fleet identity integration before implementation; no local password system or public account creation is implied. Both authentication and operator capability are required for edits. Private raw receipts additionally require diagnostic-reader capability. All private responses use no-store and are excluded from public service-worker/CDN caches.

## Shape constraints and semantic checks

| Boundary | Enforced by schema | Additional mandatory server/database check |
|---|---|---|
| Current conditions | Per-metric value/unit/source/receive/expiry/quality; null missing value has a reason; fresh values have source timestamps | Unique metric IDs; known unit/cadence; fresh iff source quality/time and server clock allow; failed DB cache retains source times and ages normally |
| Source interval | UTC Z timestamps and explicit start/end | Strict start < end; half-open membership; timezone epoch and DST/calendar identity |
| Image | Public class/opaque URL only; published/stale requires bounds, eligibility timestamps and an asset; unavailable states expose no assets | Class-to-route binding; exposure upper bound + 600 seconds <= first publication/current authorization; expiry > publication; mask/metadata sanitation; source trust |
| Video | Explicit unavailable/partial states; published/partial requires native/web/mobile, genuine poster metadata and native download path | Distinct roles/IDs, exact source/PTS map, allocation/duration arithmetic, same native bytes across routes, geometry/codec decode, metadata revision parity |
| Keogram | Segment identity, UTC bins, lineage digest and optional asset | Published/partial state requires valid keogram-class asset; x bins ordered/in-range, UTC map matches segment, explicit gaps and source-only lineage |
| Measured predicates | Whitelisted precipitation/gust/temperature statistics, units and bounded operators/coverage | Unit conversion; aggregate revision/window equality; unknown/low coverage never satisfies numeric comparison; filters applied before ranking |
| Search | Bounded query/top-k, active profile fields and explicit mode; no arbitrary SQL/formula | Count equals result length; current visibility/revision; every seek inside cited media/source interval; profile-compatible vectors; exact predicates preserved under fallback |
| Editorial | Expected revision, bounded allowed changes; no actor or raw measured fields | Server actor, authorization, CSRF/Origin, CAS, evidence rebase, source-eligible poster, immediate stale-search withdrawal and transactional audit/outbox |
| History/almanac | Bounded pages, units/evidence/coverage, gaps and revision | Stable cursor snapshot, exact supported statistic/formula, coverage interval arithmetic, history period, source-support semantics and late-evidence revision |
| Private receipts | Bounded exact-byte base64 representation and hash | Authorized diagnostic access, canonical decode/hash, complete retained payload and parser lineage, no public cache or logging |

Schema validation alone does not qualify any of these temporal, privacy or relational invariants. The [publication/recovery protocol](../implementation/publication-recovery-contract.md) and [migration contract](../implementation/database-migration-contract.md) define authoritative transaction/retention behavior.

## Time, units and missing data

UTC instants are the API comparison basis. Local display labels include offset and timezone context. Calendar-date stills and dawn-to-dawn cycle dates are separate fields. A repeated local hour has a different slot ID and UTC interval even when its hour label matches.

Current metrics have individual expiry; generated_at is response construction time, never a substitute for observed_at. During a cache-backed dependency outage data_state is cached and the server recomputes freshness without changing observed_at/received_at. A browser whose request fails continues aging its last response and labels the API unavailable.

Public metric discovery is /api/v1/weather/metrics. Registry revisions pin canonical units, display conversions and allowable statistics; private diagnostics do not enter this registry. Unknown public metric/statistic requests fail clearly instead of selecting SQL identifiers from raw strings.

Initial measured search admits precipitation observed accumulation (mm/in), maximum wind gust (m/s, km/h, mph), and temperature minimum/maximum/duration-weighted mean (degC/degF). Formula and precision policy must match the deterministic aggregate revision. Scope is the full exact video segment in v1. A semantic clip hit cannot imply the measured event occurred at that clip time. Further metrics/window scopes require a qualified versioned extension.

## Bounded queries and exports

Schema ceilings: 100 catalog entries, 5,000 history points, 100 private receipts, eight numeric predicates and 20 search results per request. These are selected protective interface ceilings, not measured capacity guarantees; D16 may tighten admitted operational budgets without allowing unbounded public work.

Require explicit UTC source bounds for history, event windows, search and almanac. The server additionally enforces configured maximum raw-window duration, aggregate point count, statement timeout, connection/concurrency and embedding rate budgets. Those values require D16 qualification before deployment. Excessive range returns a sanitized error and a suggested compatible resolution in user-facing handling; it never silently changes the statistic.

Cursors are opaque, bounded, authenticated against tampering, and bind query filters, sort key and immutable snapshot/revision. Altered filters invalidate a cursor. Late telemetry/corrections do not make a paginated export duplicate or skip records within its chosen snapshot. A new export can select the new revision. Complete history is available by successive bounded intervals/pages; query limits do not authorize deleting old data. UI export orchestration must provide resume/progress and avoid an unbounded server-memory response.

Almanac comparison requests require comparison_start and comparison_end as well as the primary interval; both must form a valid half-open interval. Other products reject those extra bounds. Comparisons retain each interval's actual coverage and duration rather than silently normalizing unequal periods.

## HTTP transport and caches

GET/HEAD authorize before ETag evaluation. HEAD has the corresponding authorized representation headers but no body; ignore Range on HEAD. GET supports one closed, open-ended or suffix byte range and correct 206/Content-Range/length. Unsatisfiable ranges return 416 with total representation length. Deliberately reject multiple ranges with bounded 400 handling. If-Range mismatch yields the full authorized representation. Strong ETags bind exact immutable bytes.

Downloads resolve the native asset through the same authorization and return attachment with sanitized filename for both full and partial content. No redirect leaves the Weather domain. Media buffering is bounded independently of object size.

Current/catalog responses may use qualified short cache/conditional requests; private editor/raw routes never do. Image embargo/expiry/withdrawal checks occur before serving any alias, thumbnail, range or HEAD. A previously public cached image cannot be recalled by a database tombstone: D14/D16 must specify maximum revocation delay and purge policy before enabling such caches. Until qualified, do not enable CDN media caching. This does not resolve the separate public transport decision.

## Acceptance ownership

WX-08 verifies schema/serialization negatives and relational constraints. WX-13 verifies complete-history pagination, metric freshness and source support. WX-19 verifies media HTTP semantics plus alternate-route privacy. WX-25/42 verify unit equivalence, incomplete/zero observations, current revision filtering and fallback. WX-37/38 verify archived derivative and current-preview timing. WX-43 verifies auth/CAS/private exclusion. WX-33 exercises these together in supported browsers.

Synthetic schema fixtures are design checks only. Server temporal checks, access controls, live source behavior, media playback and resource budgets still need their specified implementation/integration evidence. No model, camera, database or object-store service has been called by creating these contracts.

## Design validation receipt — 2026-10-23

The Draft 2020-12 metaschema check passed for the schema document. All 21 [synthetic positive/negative fixtures](schema-fixtures.json) passed, covering missing versus zero, UTC/calendar validation, numeric unit/statistic mismatch, request limits, editorial actor/measurement injection and private-original/provider-URL exclusion. Reference resolution, 16 route paths, 19 unique operations, path parameters and explicit operator security declarations were checked locally. This was a small bounded schema check, not a workload experiment.

The installed validator's optional date-time checker was absent; the initial impossible-date fixture exposed this. The successful check explicitly registered UTC/calendar assertions using Python datetime/date parsing and a UTC-Z representation with at most microsecond precision. Implementations must prove format assertion is active using the invalid-date fixture, rather than assume passing a FormatChecker object enables every optional format.

The subsequent [hash-bound validation receipt](validation-receipt.json) records a successful check against the [official OpenAPI 3.1 document schema](https://spec.openapis.org/oas/3.1/schema/2022-10-07). The three referenced JSON Schema documents and inline Schema Objects were separately metaschema-checked because OpenAPI document validation alone does not validate their complete semantics. All 371 local reference occurrences resolve, operation IDs are unique and path-parameter bindings match. All 45 shape fixtures pass: 24 API, 13 capture and eight visual-candidate cases. This resolves the outstanding structural OpenAPI check for the receipt's exact hashes. Subsequent contract edits require renewed validation. HTTP conformance, authorization, cross-record semantics, live behavior and missing configuration/archival/recovery contracts remain unproven.


## Editorial description write/public parity

`EditorWrite.changes.description` and the effective public description share a 1–8,000-character non-null limit. Reject an empty or over-limit write; never accept and silently truncate it on publication. Omission means no description change. Explicit null clears the human description override, exposing only a currently eligible validated automated description, or null if none is eligible. Clearing is an audited CAS mutation and must not resurrect invalidated evidence. Private notes retain their separate private limit and never supply a public fallback. Boundary shape fixtures do not prove CAS, attribution or withdrawal.

## Synthesis attribution

[WeatherNarrative](weather-narrative-contract.md) is a private untrusted candidate, not a public API payload. Public Claim/Event require evidence `basis` (visual/instrument/inference) separately from `origin`; inferred automated records use origin `synthesis`. Measured/visual origins require matching instrument/visual basis. Human/manual origins retain explicit evidence basis and attribution. Application-assigned interval/precision and seeks come from validated evidence, never model timestamps. Every consumer, sidecar and search projection must preserve that distinction.

## Analysis execution versus available data

[D11](../decisions/D11-analysis-execution-and-failure.md) separates `Analysis.execution_state` and sanitized `reason` from data `state`, mode and coverage. Failed/unsupported execution with no usable evidence has unavailable data; partial validated evidence can survive a failed run. Invalidated claims never become current merely because their producing run completed. Sampled-frame fallback remains degraded and cannot satisfy native-video commissioning. Cross-field validity and remote termination require semantic/runtime checks.
