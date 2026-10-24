# Public weather contracts

`weather.api.public_models` and `metric_registry` are the code-first public boundary for WX-13.
They contain no database access, credentials, collector logic or inference. `public_schema` builds
an isolated metadata/test app; it is not registered by the runtime CLI. Its data handlers deliberately
return sanitized 503 until the API owner installs real bounded readers. The discovery router can be
included by that owner independently.

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
