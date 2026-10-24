# Public weather contracts

`weather.api.public_models` and `metric_registry` are the code-first public boundary for WX-13.
They contain no database access, credentials, collector logic or inference. `public_schema` builds
an app from the real shared `public_routes.router`. The runtime factory registers it only after
explicit data activation; the default remains the health-only skeleton. Data
routes return sanitized 503 until the API owner injects a service with real bounded readers and its
existing signing key. The discovery route remains available with explicitly uncommissioned cadence.

Generate reviewed artifacts with `bash scripts/ci.sh contracts --write`. The normal lint mode
regenerates both OpenAPI and TypeScript in memory and rejects any difference. `contracts` without
`--write` performs that same read-only comparison. A focused regression overlays a real Pydantic
field change and invokes the actual CI mode, proving that stale artifacts fail. All references are
local; generation does not fetch schemas. Generated public TypeScript lives in
`web/app/generated/weather-api.ts`; it does not replace WX-20's independent boundary fixtures.

The generator uses openapi-typescript 7.13.0 and TypeScript 5.9.3 in the isolated
`web/contract-tools` frozen lock because that generator declares a TypeScript 5 peer. The app keeps
its existing TS7 compiler and TS6 compatibility API. All three locks are included in license custody.
The generator's upstream [Node API](https://openapi-ts.dev/node) returns a TypeScript AST printed by
`astToString`; no application build or service is required.

Public IDs map to numeric safe-view protocol fields in a separate internal binding table. They never
select SQL identifiers from request text and never expose raw/status/identity fields. Expected cadence
is null until commissioned; source report intervals, sensor status, rain support and lightning count
authority are not guessed by the registry. Display conversions are exact rational affine recipes:
`display = canonical × scale_numerator / scale_denominator + offset_numerator / offset_denominator`.
Definition and registry revisions hash the complete public recipes, so a conversion/statistic change
cannot reuse an unchanged identity. Station pressure stays station pressure. Solar-energy and pressure-trend results use Wh/m² and hPa/h.

Current values preserve observed/received times; `generated_at` cannot freshen expired evidence.
`age_current` reclassifies expiry without replacing measurements or source times. Missing is a reasoned
null, invalid source fields cannot become numeric values, and counts remain integers. Source clock
plausibility, calibrated support/hold policy and the configured raw-window budget remain owner inputs.
In particular, a reported lightning distance of zero with no corresponding strikes must be made
unavailable by the source adaptor; the contract does not infer a real zero-distance strike.

Raw exports are cursor-paged History objects with explicit UTC half-open bounds; resolution 0 means
unbinned observations. Aggregate requests require an explicit positive resolution and supported
statistic; unknown names/combinations return sanitized 400, never an implicit statistic switch.
Aggregate pages include their revision and preserve separate computation, evidence and coverage
revisions. Structured wind retains separate speed/direction coverage and a whole-window rose denominator;
lightning retains boundary-uncertain counts separately. Cursor authentication, snapshot consistency,
DB/cache behavior, support qualification and actual deployment remain WX-13 integration acceptance.

When WX-20 adopts these contracts, add the required conversion recipes to its strict registry fixture,
allow raw history resolution 0, and correct its synthetic Current response timestamp to follow receipt
time. The API's corresponding synthetic fixture uses 08:15:05Z rather than 08:15:00Z for a measurement
received at 08:15:01Z. No real station location or identity appears in these fixtures.

## Shared public service and runtime adoption

`public_routes.router` now contains the real async route implementation, and `contract_app` installs
that same router and exception handlers. There are no metadata-only data handlers. The production
owner calls `install_public_api(app, service)`; without an injected service data routes return a
sanitized 503 with no-store. Runtime activation is documented in [API runtime](api-runtime.md);
this source preparation enables no deployment. There is no generated/default production HMAC key.

Construct `PublicWeatherService` with a typed `PublicSource`, `CursorCodec(existing_key)`, a
commissioned raw-window budget, and the actual source-read timeout. The default outer source deadline
is five seconds; the DB adaptor still owns statement timeout, pool size and source qualification.
Pass `PublicMetrics(prometheus_client.REGISTRY)` at runtime to expose metrics on the existing metrics
listener; its default private registry isolates tests. Register the service once per process and
explicitly start its background refresh with `await service.start()` in the configured runtime
lifespan, then close it with `await service.aclose(timeout_seconds=1)` during shutdown. Construction
and OpenAPI generation never start refreshes. The background loop uses the same five-second
singleflight as HTTP calls, keeping the obs_st age signal current without public traffic. Shutdown
cancels both loop and source flight; it reports a bounded timeout if cancellation is delayed and
never publishes a late result after close. Root must still close the pool in a finally block. Source pools are owned and
closed by the runtime adaptor. A source may raise PublicInputError with a reviewed constant message
for bounded input rejection (400); never put driver/SQL/configuration text in that exception.
Unexpected source exceptions remain sanitized 503s.

Current cache refreshes are single-flight and shared for five monotonic seconds after completion.
Caller cancellation cannot cancel that shared refresh. A healthy source response marking measurements
unavailable/null replaces the old numbers; only actual read failure may replay cached evidence. A failed read is retried after the same bounded
interval; it keeps original values/source/receipt/expiry times and changes data_state to cached. Expiry
is reclassified on every response. A cold failure returns 503, including when If-None-Match is `*`; an empty unavailable placeholder
cannot become cached measurements. A failed read with no measured cache also returns 503.
The weak ETag excludes only generated_at and binds every other serialized field, including null versus
zero, quality, reasons, original times, freshness and data_state. Matching strong or weak If-None-Match
returns an empty 304 without Content-Length. Successful cache headers never exceed five seconds or the
nearest fresh expiry; cached dependency failure forces max-age=0. Errors and export pages use no-store.

`CurrentReading.newest_valid_obs_st_at` is an explicit adaptor input, never inferred from the newest
public metric or rapid wind. Its SLO gauge is absent until supplied. That gauge and per-metric ages
are computed at scrape time as now minus source time, with no zero/clamping/refresh-time substitution.
HTTP latency, source latency/errors and cache hit/miss/coalesced/cached-error counters use bounded
operation/route/status labels and never query strings, device identity, SQL or exception text.
The API role disables Uvicorn raw URL/query access logs; runtime adoption must preserve that setting.

The source returns `SourcePage[History|Aggregates]` with an atomically chosen publication watermark
and optional next position. The watermark is canonical decimal text in 0..2^63−1, from the DB's
publication boundary function. Position is at most 512 UTF-8 bytes and must encode the immutable
source-time/message-type/observation-UUID/public-metric key, including metric as the final key for
flattened points. The source receives a query with cursor=None and an already authenticated typed
Snapshot; transport tokens never double as database positions. First reads choose watermark plus rows
atomically; subsequent reads preserve that watermark and use publication_seq<=watermark, never max
UUID/receipt time or value/eligibility filters. Empty pages still return their watermark. Actual SQL
snapshot membership and late-row paging remain DB integration acceptance.

Cursors authenticate canonical UTC bounds, sorted metrics, limit, operation, explicit aggregate
resolution/statistic, schema and policy revision, watermark, position, issue time and expiry. Changing
filters, endpoint, key or policy revision invalidates them. Total token size is at most 2,048 ASCII
characters; HMAC-SHA256 is compared before decoding fields. Export expiry does not extend on subsequent
pages. Keys are supplied explicitly (32–128 bytes), never logged, and policy revision defaults to the
content-derived public registry identity. The commissioning owner must pass a combined qualified
support/computation policy revision when aggregate recipes/configuration add meaning beyond that
registry. Tokens provide integrity, not confidentiality: only public source positions belong in them.
The service rejects source page overflows, wrong intervals/statistics, nonprogress, pre-signed source
cursors and changed publication boundaries. It does not claim to prove database snapshot membership.

## Frozen WX-20 boundary alignment

A pure ASGI-to-Node serialization audit against untouched WX-20 head
`26844aa75fc3af492d062bcaa9ceff33bf72dca9` is recorded in
[evidence/public-web-alignment-20261024.json](evidence/public-web-alignment-20261024.json).
It executes the actual shared router and existing Zod parsers with Node 24.22.1, without a build,
browser, socket or database. Current defaults and cached/stale responses pass. Raw resolution=0 and
registry conversion recipes fail the existing strict parsers; diagnostic controls isolate those two
mismatches and are explicitly not production payloads. WX-20 source was not edited.

Before joint adoption, preserve the following alignments:

- Serialized defaults are present: Current.schema_version=1, MetricDefinition.public=true and
  History.aggregation=observed. Do not enable response_model_exclude_defaults/unset/none globally.
  Nullable fields remain explicit; especially expected_interval_seconds stays null until commissioned.
- Permit resolution_seconds=0 in the History boundary and render it as unbinned source observations,
  rather than a zero-second physical cadence. Add required rational affine conversions to the strict
  discovery schema/fixtures and use them for display; existing hard-coded wind/pressure factors are
  approximations and must not become a separate conversion policy.
- Fix the synthetic Current generated_at to follow receipt time (08:15:05Z for receipt 08:15:01Z).
  Canonical public IDs include station_pressure, irradiance, wind_avg and wind_direction; do not depend
  on old internal pressure/solar_radiation/air_temperature/relative_humidity aliases. The registry gives
  labels and units. Derived rain windows must not be described as new instantaneous measurements.
- Keep discovery cadence consistent with commissioned Current policy. Static discovery remains null;
  no source report interval or HTTP polling interval can silently supply physical cadence. The source
  adaptor owns null lightning distance when no corresponding strike evidence exists.
- Current-window derived IDs rain_last_hour/rain_today have no direct raw SourceBinding; their raw
  observation requests return unsupported_statistic400 before reading. The adaptor keeps those
  Current fields null with an explicit reason until qualified aggregate support is wired.
- Raw API queries accept repeated `metrics` values; WX-20's single metric is compatible. API cursor
  size is <=2,048 (within the browser's larger response bound); it signs API filters/limit, not display
  units. Changing source filters must clear the cursor. Preserve numeric null versus observed zero.
- Handle bounded application/problem+json 400/range suggestions as invalid input, distinct from 503
  availability. The current generic browser read helper collapses both. Never silently retry an
  over-range observation query with an aggregate statistic; selecting a statistic is explicit.
- Current browser fetches do not send If-None-Match, so they receive 200 bodies; a future conditional
  optimization must reuse its prior body on 304. Aggregates have a separate page shape/revision and
  explicit positive resolution; do not feed them to the raw History parser.

These are source/runtime alignment notes, not evidence of deployed API, SQL timeout/pool isolation,
real station cadence/support, valid event authority or browser integration. Those remain with WX-13
and the Slice-1 acceptance path.

## Aggregate calculation boundary

`aggregate_adapter.compute` binds the public registry to the qualified-support engine. The caller
supplies a complete, consistent support set for the requested window, including any preceding
observations required by its commissioned hold policy. A page of raw export points cannot establish
aggregate coverage. The adapter never constructs support from report cadence or chooses an event
authority. Lightning counts require the commissioned interval authority; pressure trends require
explicit endpoint width and minimum support. Reported mean wind speed is not replaced by rapid wind.

The public projection binds metric, statistic, unit and the engine recipe into its computation
revision and stable aggregate identity. Full structured wind, lightning boundary uncertainty and
pressure endpoint evidence remain attached to the internal computation for later summaries. Rain
split across a support boundary carries an explicit estimate reason. Conversion to JSON numbers
happens only after exact engine arithmetic; unsupported values remain reasoned nulls. These pure
calculations do not establish database snapshot membership or activate the revision worker.


## Complete-window support assembly

`weather.api.support_assembly` is a pure boundary between `read_evidence_window` and the shared
aggregate engine/public projection. It never queries SQL, reads an observation page, changes a
worker, or installs a runtime handler. Its `SupportPolicy` has an explicit mode and version:

- `forward_hold` requires a commissioned per-metric cap, with whole-microsecond precision. Only
  instantaneous samples can qualify a forward hold. Every eligible source-time sample in its
  device/family/metric stream, including invalid/null samples, terminates the preceding hold.
- `reported_interval` requires approved qualified policy identities and an explicit maximum report
  span for the complete reader. The evidence must already carry a qualified interval ending at
  source time with exactly the reported duration. Unknown/unqualified support remains unsupported;
  packet cadence, receipt gaps and `report_interval` alone never create it.
- Precipitation and lightning counts require reported-interval mode and one explicit family
  authority. Current unqualified rain policy tags cannot be promoted by configuration. Events never
  silently substitute for interval counts. Existing overlapping-amount reconciliation errors remain
  errors; no independent reports are silently summed or prorated as strike fractions.

Call `required_read_spans(policies)` and pass both returned bounds to the complete-window reader.
Read the full requested interval, including its bookends and following reports, before assembling
any output bucket. Never form `EvidenceWindow` from a public raw page, its cursor or its limit. The
reader fails oversized full inputs rather than returning a partial series. Source-time-ineligible
rows remain in revision provenance but do not create or terminate support. All eligible ties remain;
competing values form gaps in the shared engine, and an invalid/null tie cannot leave another member
as an implicit winner. Equal observations do not double their covered duration. Holds are per stream;
a newer sample from another device/family cannot silently truncate or select its competitor.

`assemble(engine_interval, evidence_window, policies)` returns immutable supports, policies and
revisions. `compute_window(assembly, metric, statistic, ...)` uses the existing aggregate adapter,
retains exact Decimal/Fraction results for downstream ranking, and supplies the commissioned
lightning authority. Pressure trend still requires explicit endpoint width/minimum coverage.
Boundary-straddling qualified rain is conserved and labelled as a uniform-intensity estimate;
lightning remains an integer contained subtotal with separate boundary uncertainty.

The evidence revision binds the UTC window, every selected canonical source identity,
full safe quality fingerprint, exact value, source/receipt times, support metadata and every effective
policy. It is order-independent and collapses only byte-equivalent repeated evidence identities;
differing inputs under one evidence identity fail closed. A quality correction changes this revision
even when its publication watermark and numeric output do not change. Assembly and registry recipes
are versioned separately. A newer boundary containing no changed relevant inputs retains the same
computation identity.
The snapshot boundary is retained separately for export/reproduction, rather than creating a new
historical revision for every unrelated packet. Actual evidence IDs are preserved up to the complete
input bound of 50,000; a page also bounds its total emitted references. No input is dropped or replaced
with an unresolved synthetic collection. Coverage gap overflow is an explicit bounded-range error.
Formula/support-policy changes invalidate aggregate identities,
while private metadata is never emitted. This does not implement ledger/outbox recomputation or prove
PostgreSQL snapshot consistency; those remain the root integration/qualification responsibility.

## Read-only aggregate callback and paging

`weather.api.aggregate_source.AggregateSource` is an optional callable for the SQL source's
aggregate callback. Construction requires its existing reader, an explicit `SupportPolicy` mapping,
clock, and explicit pressure endpoint options (both null when uncommissioned). It creates no pool,
worker or background computation and is not automatically installed in the application lifespan.
Root integration owns runtime configuration, the ledger/current-head worker and actual PostgreSQL
qualification.

The callback accepts any positive explicit resolution supported by `AggregateQuery`. Half-open bins
are anchored at the query start; the last bin is clipped at the actual query end. It computes integer
bin and output counts, then visits at most `query.limit` outputs (maximum 5,000), ordered by bin then
sorted public metric ID. It never allocates all bins in a long interval or substitutes a statistic.
A pressure-trend bin narrower than its commissioned pair of endpoint windows fails explicitly with a
resolution suggestion; no endpoint width is silently shortened.

Source positions use canonical `a1_` tokens containing a version, next output ordinal and UTC
microsecond evaluation timestamp. These positions are authenticated by the existing HMAC export
cursor, including canonical query filters/expiry/policy binding. The source also rejects malformed,
noncanonical, out-of-range and future positions. The first complete evidence query atomically
obtains its publication watermark even with no rows. Later pages use that same as-of-v4 watermark
and signed evaluation time: a future-tolerated source or receipt timestamp cannot age into a later
page. `read_evidence_window(..., enforce_raw_range=False)` keeps aggregate windows separate from the
raw export duration limit, while retaining statement/pool/input budgets. No raw page supplies input.

One complete bounded page envelope (up to 50,000 flattened inputs, never truncated) includes the
calibrated preceding bookends and reported lookahead. The callback indexes streams and derives a
canonical per-bin/per-metric subset, including wind dependencies, all eligible ties and invalid
boundaries. Per-output input revisions therefore remain independent of successful page size and
page boundaries for the same snapshot/evaluation/policy. They bind the actual safe quality/support
and exact values. The response-level `Aggregates.revision` names the export snapshot/query/policies;
it includes the watermark and frozen evaluation time and is not a stored computation revision.
Unrelated publications can change that snapshot receipt while unchanged closed-window item
identities remain unchanged.

Every actual evidence ID is retained. The sum of emitted evidence-ID references across the entire
page, including repeated IDs in separate outputs, is limited to 50,000. Overflow fails explicitly;
no bundle identity, dropped provenance or partial page is published. Other existing bounded DTO
limits, including at most 1,000 coverage gaps per aggregate, likewise fail as `range_too_large`.
Input and work overflow instruct the caller to choose a smaller explicit page or source interval.

A nonwaiting process-wide slot permits one aggregate callback at a time, including source I/O.
Overload is a sanitized 503; it never queues an unbounded set of computations. The default work
budget is 250,000 charged work units (constructor bound at one million); index work
cooperatively yields each 128 charged operations and every output yields before computation.
Before synchronous engine math, a cooperative endpoint preflight additionally charges the
active-support cardinality/sort cost at each boundary, with conservative wind/pressure sweep
multipliers. This prevents staggered long overlapping holds from bypassing the work budget through
the engine's quadratic active-set work. Assembly and engine calls retain the complete input cap,
with no detached threads or tasks.
Cancellation releases the slot; bounded `aclose()` cancels its active caller and prevents further
reads or late publication. The existing service timeout remains the outer five-second guard.
Actual source SQL stress, maximum-density memory/CPU and cancellation latency require in-cluster
qualification; fake-reader tests do not establish those performance bounds.
