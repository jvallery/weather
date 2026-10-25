# HTTP and schema contract

Status: the semantic HTTP contract (P12). Implemented routes are code-first: the Pydantic models generate [weather.openapi.json](weather.openapi.json), which is the shape authority. Sections for routes not yet built describe the intended semantics; the issue that builds a route owns its tests. The hand-written planning [OpenAPI](../archive/planning-2026-10/contracts/openapi.json) and [JSON Schema](../archive/planning-2026-10/contracts/api.schema.json) are reference input only. Schemas describe allowed shapes, not authorization.

The private [capture contract](capture-contract.md) is separate from public serializers. The private candidate [visual-model schema](../../app/weather/ai/contracts/visual-analysis.schema.json) implements §13.5's shape; its semantic checks are specified below.

## Weather data routes (implemented, #14)

The weather routes are code-first: the Pydantic models in `app/weather/api/models.py` generate [weather.openapi.json](weather.openapi.json), and `openapi-typescript` generates `web/app/generated/weather-api.ts`. `scripts/ci.sh lint` fails when either is stale (`scripts/ci.sh contracts --write` regenerates both). The web parses leniently: it checks the fields it reads and drops unknown ones, so an additive field is not an outage. Business invariants live only in the server models. Golden aggregation vectors: [aggregation-vectors.json](aggregation-vectors.json).

| Route | Contract |
|---|---|
| `GET /api/v1/weather/current` | `generated_at`, `station_alias`, `data_state` (`live`/`cached`/`unavailable`) and one entry per metric: `value` (null is distinct from 0), `unit`, `observed_at`, `received_at`, `expected_interval_seconds`, `valid_until` (= `observed_at` + hold), `quality` (`valid`; `invalid` when the row or that field failed validation; `missing` when absent), `freshness`, `reason`; rain totals add `coverage`, computed for the instant the totals were read, and `rain_today` is withheld once the weather day rolls over until a new total is read. Weak `ETag` with `If-None-Match` → 304; `Cache-Control: public, max-age` ≤ 5 s (0 while cached). |
| `GET /api/v1/weather/metrics` | Registry: canonical unit, rational conversions, statistics, expected cadence. |
| `GET /api/v1/weather/observations` | `start`, `end` (UTC `Z`), `metrics` from one source family, `limit` ≤ 5,000 points, `cursor`. Raw window ≤ 31 days for `obs_st`, 1 day for `rapid_wind`; longer → 400 `range_too_large` with `suggested_resolution_seconds`. |
| `GET /api/v1/weather/aggregates` | `start`, `end`, `metrics` (≤ 8), `statistics` (≤ 4), `resolution` (seconds ≥ 60 aligned to the Unix epoch, or `day` for America/Denver days). ≤ 5,000 items; the window (≤ 92 days for `obs_st`, 7 for `rapid_wind`) is checked before any bins are built. All instants must fall within 2000–2100. Each item: `value`, `unit`, `coverage` (fraction, support and expected seconds), `evidence_count`, `evidence_hash`, `reason`; wind adds `direction_state` and `rose`. |
| `GET /api/v1/weather/almanac` | `product` (`record`, `rain_day`, `wind_rose_bin`, `astronomy`, `comparison`), `start`, `end` at America/Denver midnight, ≤ 366 local days. `record` and `comparison` require `metric` and `statistic` (records: min/max/sum; comparisons: mean/min/max/sum; `obs_st` metrics only); comparisons also require `comparison_start` and `comparison_end`, which other products reject (400). `limit` ≤ 400 items and the signed `cursor`. The response carries `interval`, `history_interval` (null without history), `timezone`, `formula_revision`, `library_revision`, `evidence_count`/`evidence_hash`, product `coverage` and `missing_reasons`, and items discriminated by `product`. Computed on read from the aggregates above (#42). |

| `GET /api/v1/events` | Weather events (#41): `day` (America/Denver date), `camera` (that camera plus station-wide), `origin`, `order` (`asc`/`desc`), `limit` ≤ 100 (default 50), `cursor`. Each event: opaque `id`, `revision`, `kind`, `label`, `origin`/`basis`, `human_edited`, `rule_revision`, `precision`, half-open `source_interval`, `camera` (null = station-wide), `local_dates`, `evidence_ids`, gust/strike/distance/coverage where measured, and per-camera `videos` (`available` with a seek inside both intervals, or `unavailable`). Cursors carry the first page's exact PostgreSQL snapshot. `no-store`. |
| `GET /api/v1/cameras/{camera}/preview` | Delayed preview pointer (#39), served by `weather-preview`: `state` (`fresh` ≤1,200 s, `stale` ≤1,800 s, `unavailable`) and `reason` (`no_preview`, `too_old`, `media_unavailable`), `generated_at` (database clock), `generation`, capture `source_interval` and `exposure_upper_bound`, `public_not_before`, `first_published_at`, `expires_at`, `age_seconds` (from the lower bound), the policy windows and up to two `images` (`large`/`small`, `/media/previews/{id}`, JPEG). Unavailable carries no images. Unknown camera 404, catalog failure 503. `no-store`. Bytes at `/media/previews/{id}` follow the [media origin](#media-origin-implemented-20) and stay eligible until `expires_at`. [Policy](preview-policy-contract.md). |
| `GET /api/v1/events/{id}` | The current public revision of one event; 404 `not_found` (identical for missing, private and withdrawn). |

Errors are `application/problem+json` with `code`, `message` and optional `suggested_resolution_seconds`: 400 for `invalid_request`, `unknown_metric`, `unsupported_statistic`, `invalid_cursor` and `range_too_large`, and 503 `unavailable` for any source failure or timeout. Nothing from the driver, SQL or configuration is echoed.

Cursors are HMAC-SHA256 tokens bound to the filters (start, end, metrics, limit). They carry the keyset position (`observed_at`, `id`) and the insert-time snapshot of the first page (see Bounded queries below), so rows that land during an export, including late spool-drain or reproject rows with an old `received_at`, never duplicate or shift it. Expiry slides one hour per page, capped at 24 hours from the first page.

Freshness: `obs_st` metrics expect 60 s and stay fresh for 150 s; `rapid_wind` expects 3 s and stays fresh for 15 s (configurable defaults). Freshness is recomputed from source time on every response; a replayed old packet is never newer than the current row, so it freshens nothing. While the database is down the API serves its last reading as `cached` with the original times until no metric is fresh, then 503; with no reading it returns 503.

Aggregates follow the [aggregation contract](weather-aggregation-contract.md). Per C5 they carry an evidence count and a SHA-256 over the contributing row IDs, not ID lists, and no revision binding: they are recomputed on every read.

## Search (implemented, #25)

`POST /api/v1/search` takes `start`, `end` (UTC `Z`), `query` (≤512 characters; no control characters), `cameras`, `segments` (`daytime`/`overnight`), `predicates` (≤8) and `top_k` (1–20, default 10); unknown fields are rejected. A predicate is `metric`/`statistic` (`precipitation`/`sum`, `wind_gust`/`max`, `temperature`/`min`, `max`, `mean`), `op` (`<`, `<=`, `=`, `>=`, `>`), `value`, `unit` (a registry unit of that metric) and `min_coverage` (default 0.95). Predicates apply to the whole video interval over the on-read aggregates and need a window of at most 31 days; a missing or under-covered value never matches.

The response has `mode` (`hybrid_exact`, `lexical`, `numeric`), `degraded_reason` (`embedding_unavailable`, `embedding_busy`, `lexical_no_terms` or null), `profile_id` (set for `hybrid_exact`), `count` and `hits`. Each hit has `rank`, `video_id`, `document_id`, `camera_id`, `segment`, `video_interval`, `source_interval`, `evidence_precision`, `analysis_mode`, `snippet`, `media_revision`, `poster_asset_id`, a nullable `seek` (`media_seconds`, `source_at` inside the cited interval) and `matched_measurements`. Errors: 400 invalid input or `range_too_large`, 429 `busy` with `Retry-After`, 503 `unavailable` for a SQL failure or deadline (never an empty success). Every response is `no-store`. Behaviour and limits: [search operations](../operations/search.md).

## Effective editorial content and analysis coverage

`EditorialPublic.description` is the effective, currently eligible description, whether generated or human-edited; null means no eligible description. It is separate from immutable automated claims. `human_edited` and the effective revision label its provenance. An invalidated evidence binding cannot remain visible merely because this string still validates. Public sidecars, UI and lexical/semantic indexing must consume the same effective revision. Private notes and favorites remain excluded. The 8,000-character interface bound is a protective ceiling, not permission to bypass grounding.

`Analysis.coverage` separately describes supported reviewed source duration, with gaps and algorithm identity. Capture coverage does not establish analysis coverage. Union reviewed, valid source windows; do not count repeated/overlapping clips twice. Pending/unavailable work has zero supported duration, while actual unassessable media remains explicitly limited; do not claim full visual evidence solely because the request included the entire video. The coverage algorithm must define its assessability policy and preserve per-claim limitations.

## Private visual-model candidate boundary

[visual-analysis.schema.json](../../app/weather/ai/contracts/visual-analysis.schema.json) enforces the exact category/assessment/confidence vocabulary, at most 16 observations, eight limitations and 240 characters per description/limitation. Evidence lists are nonempty, unique and bounded at 256 opaque IDs. This last ceiling is a selected interface protection; the analysis worker (P9) keeps actual per-request evidence below it.

Schema-valid model text is still untrusted. Check all evidence IDs against the request's supplied set, each observation's IDs against the result's declared set, and the supplied IDs against the current media/evidence revision. Rebind candidate observation IDs to deterministic application IDs. Require explicit precipitation and visibility assessments when assessable, and an honest not-assessable result when night/obstruction prevents assessment. Reject invented IDs, unsupported claims, prompt-injected instructions, truncation and stale jobs before publishing. No numeric confidence percentage replaces the categorical field. The schema is private candidate input; it is not the public Analysis representation and must never be returned raw to browsers.

The [visual fixtures](visual-analysis-fixtures.json) exercise shape limits only. Semantic fixtures must include a schema-valid invented evidence ID, omitted assessable precipitation/visibility, repeated generated IDs across clips, scene prompt injection, stale revision and forward/reverse temporal controls. Passing the shape suite does not establish grounding.

## Routes and compatibility

All data routes use /api/v1; media/download paths are same-origin opaque-ID paths. Search uses POST for its bounded structured read request; shareable UI URLs serialize the same typed filters without implying a mutating operation. Basic catalog links remain usable without JavaScript.

Weather data responses may gain additive fields within v1; clients parse leniently (see above). Editorial and search input still reject unknown properties. Media and metadata revisions remain distinct from schema version. Errors use a bounded code/message/request ID with no stack, SQL, device identity, bucket key or model payload.

A public ID is not a capability. The media resolver checks class, committed publication, trusted exposure eligibility, expiry and tombstone before returning headers or bytes. It cannot accept arbitrary upstream URLs/keys. IDs in operator routes similarly require subject-specific authorization.

Operator routes (`/editor`, `/api/v1/operator/*`) sit behind Authentik forward-auth (P11). The API trusts the identity header only from Traefik (enforced by NetworkPolicy) and admits a request only when that subject is on the one-user allowlist; there are no capability roles, local passwords or public accounts. Mutations additionally require `SameSite=Strict` cookies and a matching `Origin`. All private responses, including private raw receipts, use no-store and are excluded from public service-worker/CDN caches.

## Shape constraints and semantic checks

| Boundary | Enforced by schema | Additional mandatory server/database check |
|---|---|---|
| Current conditions | Per-metric value/unit/source/receive/expiry/quality; null missing value has a reason; fresh values have source timestamps | Unique metric IDs; known unit/cadence; fresh iff source quality/time and server clock allow; failed DB cache retains source times and ages normally |
| Source interval | UTC Z timestamps and explicit start/end | Strict start < end; half-open membership; timezone epoch and DST/calendar identity |
| Image | Public class/opaque URL only; published/stale requires bounds, eligibility timestamps and an asset; unavailable states expose no assets | Class-to-route binding; exposure upper bound + 600 seconds <= first publication/current authorization; expiry > publication; mask/metadata sanitation; source trust |
| Video | Explicit unavailable/partial states; published/partial requires native/web/mobile, genuine poster metadata and native download path | Distinct roles/IDs, exact source/PTS map, allocation/duration arithmetic, same native bytes across routes, geometry/codec decode, metadata revision parity |
| Keogram | Segment identity, UTC bins, lineage digest and optional asset | Published/partial state requires valid keogram-class asset; x bins ordered/in-range, UTC map matches segment, explicit gaps and source-only lineage |
| Measured predicates | Whitelisted precipitation/gust/temperature statistics, units and bounded operators/coverage | Unit conversion; aggregate window equality and evidence count/hash; unknown/low coverage never satisfies numeric comparison; filters applied before ranking |
| Search | Bounded query/top-k, active profile fields and explicit mode; no arbitrary SQL/formula | Count equals result length; current visibility/revision; every seek inside cited media/source interval; profile-compatible vectors; exact predicates preserved under fallback |
| Editorial | Expected revision, bounded allowed changes; no actor or raw measured fields | Server actor from the allowlisted identity, Origin check, CAS, evidence rebase, immediate stale-search withdrawal and transactional audit/outbox. Poster choice is #78: frames from the published rendition, embargoed and EXIF-stripped, never overwritten by a pipeline or AI rerun |
| History/almanac | Bounded pages, units, coverage and evidence count/hash | Stable cursor snapshot, exact supported statistic/formula, coverage interval arithmetic and source-support semantics; aggregates are recomputed on read (C5) |
| Private receipts | Bounded exact-byte base64 representation and hash | Allowlisted operator only, canonical decode/hash, complete retained payload and parser lineage, no public cache or logging |

Schema validation alone does not establish any of these temporal, privacy or relational invariants; the database CHECKs, views and server checks do.

## Time, units and missing data

UTC instants are the API comparison basis. Local display labels include offset and timezone context. Calendar-date stills and dawn-to-dawn cycle dates are separate fields. A repeated local hour has a different slot ID and UTC interval even when its hour label matches.

Current metrics have individual expiry; generated_at is response construction time, never a substitute for observed_at. During a cache-backed dependency outage data_state is cached and the server recomputes freshness without changing observed_at/received_at. A browser whose request fails continues aging its last response and labels the API unavailable.

Public metric discovery is /api/v1/weather/metrics. Registry revisions pin canonical units, display conversions and allowable statistics; private diagnostics do not enter this registry. Unknown public metric/statistic requests fail clearly instead of selecting SQL identifiers from raw strings.

Initial measured search admits precipitation observed accumulation (mm/in), maximum wind gust (m/s, km/h, mph), and temperature minimum/maximum/duration-weighted mean (degC/degF). Formula and precision policy match the on-read aggregates and their golden vectors (C5). Scope is the full exact video segment in v1. A semantic clip hit cannot imply the measured event occurred at that clip time. Further metrics or window scopes are a versioned extension.

## Bounded queries and exports

Schema ceilings: 100 catalog entries, 5,000 history points, 100 private receipts, eight numeric predicates and 20 search results per request. These are protective interface ceilings, not measured capacity guarantees; operational budgets may be tightened but never removed.

Require explicit UTC source bounds for history, event windows, search and almanac. The server additionally enforces configured maximum raw-window duration, aggregate point count, statement timeout, connection/concurrency and embedding rate budgets. Excessive range returns a sanitized error and a suggested compatible resolution in user-facing handling; it never silently changes the statistic.

Cursors are opaque, bounded, authenticated against tampering, and bind query filters, sort key and an insert-time snapshot. The snapshot is the database clock on the first page minus 120 s (longer than any ingest or reproject transaction), compared with each row's database-assigned `inserted_at`, never the receiver's `received_at`; so late rows from a spool drain or reproject neither duplicate nor skip records within an export, and the newest two minutes appear only in a new export. Altered filters invalidate a cursor. Complete history is available by successive bounded intervals/pages; query limits do not authorize deleting old data. UI export orchestration must provide resume/progress and avoid an unbounded server-memory response.

Almanac comparison requests require comparison_start and comparison_end as well as the primary interval; both must form a valid half-open interval. Other products reject those extra bounds. Comparisons retain each interval's actual coverage and duration rather than silently normalizing unequal periods.

Search uses explicit English plain-text matching, no-terms handling and finite request/SQL/model-wait budgets, with no query-vector cache (P10) and the [request ceilings carried from D12](../architecture.md#rules-carried-from-adrs). Search responses/errors use no-store. Nonempty no-terms text never silently becomes numeric browsing; without semantic availability return lexical mode with reason `lexical_no_terms`. SQL timeout or failed current-revision hydration is unavailable (503), admission/rate rejection is 429, and invalid input is 400.

## HTTP transport and caches

### Media origin (implemented, #20)

The `api` serves `/media/{asset_id}` and `/downloads/{video_id}/native`; #39's `weather-preview` reuses the same origin code for previews. Each path admits exactly one canonical lowercase UUID; any other spelling under those prefixes (escapes, dot segments, extra segments, object keys) is a denial. The one fixed exception is the probe target `/media/latest/{kind}` (#49), `kind` one of `video` (web rendition), `poster`, `keogram`, `still`: the newest such asset, from a source that ended within 14 days, that `public_api.videos` or `public_api.stills` lists, re-resolved on every request with the same headers and rules as `/media/{asset_id}`, and 404 when none qualifies. No caller-supplied key, path or URL is ever used, and there are no redirects, presigned URLs or listings.

Authorization runs at request time for every GET, HEAD and range against the committed catalog on the primary, with the database clock (P8). A media ID serves only a row of `public_api.assets`: a public class (video rendition, poster, keogram, still, sidecar) that is available, not withdrawn and has `published_at` and `public_not_before` at or before now. A download serves only the native rendition of a video `public_api.videos` lists with media. Nothing about the decision is cached, so a withdrawal takes effect on the next request. There are no mid-stream authorization leases: a response that started may finish.

Every denial (unknown, embargoed, unpublished, withdrawn, original, wrong class, malformed path) is the same 404 problem document, with the same headers and no ETag, over GET, HEAD and Range. A catalog or store failure before the headers is 503 with `Retry-After`, no media headers and no body. After the headers, a failure aborts the connection; nothing is appended to media bytes.

An eligible response carries a strong ETag (the quoted catalog SHA-256), `Accept-Ranges: bytes`, the exact Content-Length, the catalog MIME type, `Cache-Control: no-store, no-transform` and `X-Content-Type-Options: nosniff`. Downloads add `Content-Disposition: attachment; filename="weather-<camera>-<cycle-date>-<segment>-native.mp4"` for full and partial responses. There is no Last-Modified.

### Range policy

GET supports one closed, open-ended or suffix byte range with 206 and Content-Range. A weak, dated or nonmatching If-Range serves the full 200; only the exact strong ETag honours Range. Unknown range units are ignored. Malformed byte syntax, reversed endpoints or multiple ranges are 400. A valid range that starts at or past the end, or `bytes=-0`, is 416 with `Content-Range: bytes */length`. An oversized end is clamped to the last byte; an oversized suffix returns the whole representation as 206. HEAD ignores Range and If-Range entirely, including malformed values, and has no body. If-Match, If-None-Match, If-Modified-Since and If-Unmodified-Since are ignored: responses are `no-store`, so 304 and 412 would serve no purpose.

Examples (100-byte eligible asset):

| Input | Expected |
|---|---|
| HEAD with `bytes=100-` or multiple ranges | 200, length 100, no body or Content-Range |
| GET `bytes=0-0` | 206, `bytes 0-0/100`, length 1 |
| GET `bytes=90-999` | 206, `bytes 90-99/100`, length 10 |
| GET `bytes=-200` | 206, `bytes 0-99/100`, length 100 |
| GET `bytes=100-` or `bytes=-0` | 416, `Content-Range: bytes */100` |
| GET reversed endpoints or two byte ranges | 400, no media body |
| GET with matching If-None-Match | 200, full body |
| Nonmatching If-Range plus unsatisfiable Range | 200, full length 100 |
| Withdrawn or embargoed asset, any validators or range | The 404 denial |

Integer parsing is bounded and nothing is allocated from a supplied offset. Header size is bounded by the server before any semantic handling. Streams are bounded per pod and per visitor; each stream holds one upstream chunk and waits for the client, a disconnect cancels the upstream read, and a client that stops reading for 60 s is cut off.

### Caches

Media bytes, downloads, previews and preview pointers are `no-store`, and shared edge caching is bypassed (D14's initial cache policy), including errors. Cloudflare caching is not pursued (R25); a later cache would need its own exposure and invalidation design. Current/catalog responses may use short, qualified caching; private editor and raw routes never do. Already downloaded bytes cannot be recalled.

## Acceptance ownership

Each route's issue owns its tests in its acceptance list (P16): #14 weather data, #20 media origin, #25 search, #39 previews, #42 almanac, #44 and #78 editor.

## Editorial description write/public parity

`EditorWrite.changes.description` and the effective public description share a 1–8,000-character non-null limit. Reject an empty or over-limit write; never accept and silently truncate it on publication. Omission means no description change. Explicit null clears the human description override, exposing only a currently eligible validated automated description, or null if none is eligible. Clearing is an audited CAS mutation and must not resurrect invalidated evidence. Private notes retain their separate private limit and never supply a public fallback. Boundary shape fixtures do not prove CAS, attribution or withdrawal.

## Synthesis attribution

[WeatherNarrative](weather-narrative-contract.md) is a private untrusted candidate, not a public API payload. Public Claim/Event require evidence `basis` (visual/instrument/inference) separately from `origin`; inferred automated records use origin `synthesis`. Measured/visual origins require matching instrument/visual basis. Human/manual origins retain explicit evidence basis and attribution. Application-assigned interval/precision and seeks come from validated evidence, never model timestamps. Every consumer, sidecar and search projection must preserve that distinction.

## Analysis execution versus available data (implemented, #23)

Each `Timelapse` carries `analysis`: the fixed `label` "AI-generated · sampled-frame analysis", `state` (`pending`, `complete`, `degraded`, `failed`, or `unavailable` without playable media), a sanitized `reason` code, `stale`, `revision`, `generated_at`, the effective `description`, `claims` (text plus `basis` only), `limitations`, `coverage` (reviewed seconds and hour windows, gaps, algorithm) and `provenance` (model route and echoed model, prompt and schema revisions, refresh kind, evidence count, frames sampled, keogram used). Content appears only for a validated revision of the video's current media; a new rendition shows `pending` with no text. Evidence IDs, observations and raw replies are never served. Analysis uses sampled frames plus the keogram when present (P9) and is pinned to the model and prompt version at publication. Runbook: [intelligence](../operations/intelligence.md).

## Preview expiry clarification

`Image.expires_at` is logical derivative expiry, not current-preview freshness or permission to delete storage. The [preview policy contract](preview-policy-contract.md) distinguishes earliest publication (P14), current-display age and retention. A latest-preview response above its current-age cutoff is unavailable with no assets even while a historical direct asset is still eligible. Clients must age already-received current previews during failed polling. Origin authorization checks logical expiry/withdrawal before every GET, HEAD and range response.

## Exact time ownership

The [section 5 audit](../archive/planning-2026-10/implementation/time-clause-audit.md) binds civil schedules, calendar/hour identities and frame ownership. Interval ordering and membership are semantic, not JSON Schema guarantees. Derive seeks from validated rational source/frame maps; decimal browser seek values never define capture/clip ownership. Exclusive endpoints and explicit UI clamping must not introduce an adjacent-segment sample.

## Wind-rose structure

Almanac wind_rose_bin products require structured category/speed bounds/duration/denominator; other product kinds reject that metadata. The [aggregation contract](weather-aggregation-contract.md) defines whole-window denominators, north-wrap bins, calm/variable/missing distinctions and semantic conservation checks. Labels are presentation, not a parseable calculation contract.

## Event identity and provenance

The [event contract](weather-event-contract.md) defines initial onset/gust/lightning rules, stable logical identity and revision/withdrawal behavior. Public events require unique nonempty calendar membership; measured/visual/synthesis events additionally require unique nonempty evidence IDs. Private manual audit remains mandatory even when a standalone manual annotation has no prior instrument evidence. Schema presence is not proof of evidence validity or membership.

## Search evidence precision independent of playback

Every `SearchHit` carries required `evidence_precision` (`frame`, `interval`, `clip`, or `segment`) alongside its evidence-supported `source_interval`, even when `seek` is null because playback is unavailable. Precision describes the evidence, not the resolution of a numeric timestamp. Do not infer frame-level evidence merely because the player can seek to a frame. When a seek exists, its precision and source interval must agree with the hit’s cited evidence; enforce that cross-field relation semantically. Whole-video measured predicates retain their own aggregate interval and evidence count/hash in `matched_measurements`, which may be broader than the visual hit. The client must label both scopes without presenting a whole-segment gust as a clip-time event.

The draft v1 shape was corrected before implementation; consumers and generated types must include this required field.

## Aggregate computation provenance

Superseded by C5 (#14). Aggregates are computed on read, so there is no stored computation or evidence revision to bind. Each `Aggregate` carries `evidence_count` and `evidence_hash` (SHA-256 over the contributing row IDs in source order), which change whenever the inputs change. A formula change is a code change reviewed in the PR that makes it, with the golden vectors updated alongside.
