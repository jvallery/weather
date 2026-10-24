# Public SQL reader source (WX-13)

This is an isolated source adaptor for the service protocol in `c70ffa0`, after
the database source in `71003ba`. It requires #9, #12 and migration
`0003_current_publication`, plus `0004_evidence_revisions` for complete-window
aggregate evidence; it does not compose their unmerged models into #9,
activate a workload, implement HTTP/cache/HMAC, or install an aggregate handler.
All PostgreSQL reader proof cases remain **unrun**.

## Production connection and lifetime

`weather.db.public_connection.create_public_database_engine()` creates a lazy
SQLAlchemy 2.0.46 / asyncpg 0.32.0 engine. It does not connect during import or
construction. It reads only `username`, `password` and `ca.crt` beneath the
projected credential directory (default `/var/run/weather-db`). The username
must be `weather_api`; migrator, worker and ingest credentials are rejected.
There is no production URL, host, role or CI-environment override.

The fixed target is `weather-pg-rw:5432/weather`. Its SSL context requires a
trusted certificate chain and hostname verification against that DNS name;
asyncpg receives the strict context and requires TLS, without an allow/prefer
plaintext fallback. SQLAlchemy hides parameters and disables echo. Neither
configuration errors nor reader failures expose driver exception text, SQL
parameters, credentials or raw source data.

| Budget | Default / invariant |
|---|---|
| Pool | 4 connections per process, zero overflow; configurable only downward |
| Pool wait | 250 ms, maximum 500 ms |
| Connect | 2 s maximum |
| Statement | 1500 ms; maximum 2000 ms, set both for the session and each read transaction |
| Read transaction | Explicit `READ ONLY`; no connection/transaction remains open across pages |
| Lock / idle transaction | 500 ms / 3000 ms session safety bounds |

CNPG #6 defines `weather_api.connectionLimit=40` and `max_connections=120`.
Two API processes use at most eight connections; a third rolling process uses
twelve. Keep `(replicas + surge) * pool_size` below that shared role limit,
preserving room for operations and other database roles. Never reuse this
read-only pool for later operator writes.

Construct `PublicSqlSource` with that engine, a commissioned public station
alias, an explicit maximum raw-window length, and per-metric `FreshnessPolicy`
objects. Pass the same budget to engine and source. The service owns its cache,
signer, metrics and background refresh. Root integration owns lifespan: stop the
service refresh before calling `source.aclose()` to dispose the pool.

## Current conditions

One bounded statement reads the safe current view and separately selects the
newest valid `obs_st` source time. That scalar requires the configured station,
a valid row, at least one qualified numeric registry-bound weather field, and
source/receive timestamps no later than the read clock. It is never inferred
from rapid wind, legacy families, status packets or the newest arbitrary
current pointer. Missing evidence returns `None`, omitting the SLO age metric.

The pure mapper uses canonical registry units and original source/receive
times. It preserves numeric zero and exact integer lightning counts by decoding
PostgreSQL numeric text; counts never pass through floating point. A newer
invalid or disputed field yields a healthy null/reason DTO, so the service
replaces prior numbers rather than treating bad measurements as a DB outage.
Future source/receive clocks cannot produce fresh values or negative age.
Lightning distance additionally requires a qualified positive integer strike
count from the same observation and family. Missing, zero, corrupt or disputed
companion counts produce a constant null reason; a real zero distance with a
positive qualified count remains valid, and zero counts remain valid counts.
Current joins the safe observation by its ID and family, never the independent
current count pointer. Raw/history and aggregate evidence extract the companion
from the same safe row (the same immutable revision at W for aggregates). Only
safe numeric/kind/field-quality companion columns enter the internal mapper;
they are absent from public DTOs. Unavailable distance has no exact support
value, and its evidence quality hash binds the safe companion qualification.

Freshness is commissioned separately for each metric: explicit expected
interval and hold cap, or neither. It never comes from `report_interval`.
Without that policy a valid measurement is stale with an uncommissioned reason;
with it, expiry is original source time plus the cap. Replays and repeated reads
change neither timestamp nor expiry. Unconfigured derived current metrics stay
null with an explicit reason.

## Observation pages

The source consumes `ObservationQuery` with `cursor=None` and an optional
authenticated `Snapshot`. The companion service alone signs/verifies cursors.
Unsigned source positions use a bounded canonical `p1_` encoding of UTC source
time, family, observation UUID and public metric. Including metric prevents a
one-point page from skipping a second metric from the same observation.

The first SQL statement chooses its nonlocking publication watermark and rows
atomically using a MATERIALIZED boundary CTE. A left join returns the watermark
even for an empty page. Later pages bind the same bigint boundary and immutable
flattened key, ordered with explicit C collation. All filters, metric bindings,
window bounds, limits and positions are bound values; no request input becomes
an SQL identifier or statement fragment. Fetching one extra point determines
continuation while returned pages stay within the 5000-point contract.

Only `public_api` safe views and the scalar watermark are queried. Eligibility,
value, freshness and support are never membership predicates: later conflicts
keep their point/position and return null/reason. Evidence IDs hash the immutable
family/observation/metric identity, so invalidation does not create a new member.
Derived metrics without direct source bindings receive `unsupported_statistic`
instead of an invented raw series or a silent statistic change.

Per-point support is present only for explicitly qualified v2 preceding/report
interval metadata with representable positive duration. Current freshness caps
do not fabricate aggregate support. Raw pages return no inferred gap/coverage
claim from transport-arrival diagnostics or their limited page subset.
`aggregates()` delegates an optional injected `aggregate_source(query,snapshot)`
awaitable; without one it raises a sanitized unavailable error until root
integrates the real aggregate source/handler. The reader implements no duplicate
aggregate math.

### Complete-window evidence

`weather.db.public_evidence.read_evidence_window(reader, window, metrics, *,
preceding_hold_seconds, following_report_seconds, snapshot_watermark=None,
evaluated_at=None, enforce_raw_range=True, maximum_points=5000)` is an internal
complete read, never a raw page subset.
Spans are explicit calibrated integers from zero through 86400 seconds and must
cover every forward hold and preceding-report interval the caller accepts.
The strictly boolean `enforce_raw_range` defaults to the raw-export duration
limit. Internal aggregate callers explicitly set it to `False` for coarse bins
longer than that limit. This changes no raw endpoint policy and does not bypass
the exact 50000-input ceiling, fail-on-overflow behavior, or database statement,
pool and source-selection bounds. There is no silent truncation or resolution switch.

It atomically reads/binds W, all selected rows from start through the inclusive
end-plus-report span, and all ties at the latest source-time-eligible timestamp
before start per public device/family/metric within the hold span. It never
chooses authority by UUID or drops field-invalid/null boundaries. Bad source
times do not replace valid preceding boundaries; inside-window rows retain their
explicit source-time flag.
For canonical v2 rows, top-level safe `quality.valid` represents source-time
eligibility: quarantine/invalid source precision never produces a canonical row,
and a future source clock clears it. Measurement/sensor/null/conflict validity
stays per-field; qualification changes only those fields. Thus a field-invalid
but source-time-valid sample remains a hold-breaking boundary.

`EvidenceWindow` contains `watermark` and `rows`. Each frozen `SupportEvidence`
contains a safe `HistoryPoint`, qualified `exact_value: Decimal|None`, opaque
`quality_revision` hash over only the complete normalized safe field/row quality,
observation UUID, public family/device alias, publication sequence,
`source_time_eligible`, and normalized reported-support kind/policy/qualification/
seconds. Exact values come only from already-safe numeric text when the point
is qualified; disputed/future/invalid values remain unavailable. Public float
underflow cannot turn a nonzero source measurement into zero. Unknown and
explicitly unqualified policies do not create support; the current v2 qualified
reported policy is `v171-wind-report-interval-1`.

The complete-window budget is explicit (maximum 50000 internal points). Above
it, the read fails instead of returning partial input; split worker windows when
needed. The caller owns authority, calibrated hold assembly, overlap/coverage/
statistic math, resource bounds and publication fencing. Under 0004, effective
qualification changes advance the shared clock and append immutable safe states.
The helper selects the latest `revision_seq<=W` per publication from
`public_api.telemetry_evidence_revisions`, fixing aggregate values/quality as well
as membership. Its input hash still binds exact/null/quality/support and
calibration state, not W alone. No connection remains held after return.
`evaluated_at` defaults to the reader clock captured once for the read. An
explicit value must be a UTC-aware Python datetime no later than the current
reader clock; rejection uses a constant message without the supplied value.
Both SQL source-time qualification and point mapping use this same exact instant.
The aggregate adapter binds its first evaluation instant at microsecond precision
into its signed opaque continuation position and reuses it with W. Thus tolerated
future source/receipt times cannot age into eligibility or replace a preceding
hold boundary within one snapshot. The helper accepts no unsigned HTTP time input.
Raw observation pages retain their live-eligibility v3 view so
later disputes return null/reason without dropping snapshot members.

## Failure handling and proof limits

Pool exhaustion, statement timeout, disconnect and unexpected mapping failures
raise a constant `PublicReadUnavailable` without logging or retaining a message
for the public response. The companion service records bounded source-error and
latency metrics, ages a cached response using its original timestamps, and
returns sanitized 503 when no cache exists. The reader has no fallback numbers.
Explicit input errors retain their reviewed constant messages for 400 handling.

For connection failures, inspect Secret **names/keys**, projected file existence
and ownership, CA chain/hostname, role readiness, CNPG health and pool/statement
budgets. Never print credential files or exception/URL parameters. Restore the
declarative configuration; do not weaken verification or borrow a stronger role.

Eleven focused pure tests cover numeric/clock/quality mapping, commissioned
expiry, support qualification, canonical positions, bound query parameters,
strict TLS configuration, pool bounds, empty-page shape, complete-window bounds,
exact decimals, unknown-support exclusion, explicit budget failure and sanitization.
They open no socket and execute no SQL. They were checked against exact service
source `c70ffa0138fe85383f31f33c4fe9dfca533ba738` through a temporary dependency
overlay; that service's files are not duplicated in this commit.

Four additional guarded integration cases are **unrun**: real API-role
current/observation reads including zero and large integer counts plus explicit
`obs_st` authority; a 30-day flattened export with late rows and same-transaction
conflict invalidation plus empty boundaries; and API pool saturation/statement
timeout while ingest commits independently and subsequent reads remain read-only;
and complete-window reads preserving invalid tied bookends, following reported
support, and immutable qualification at the frozen boundary while newer reads
observe the later dispute.
They reuse the socket/version/marker guard and reference-seed cleanup from the
v3 migration tests. TLS negotiation against live CNPG remains a commissioning
check; the disposable socket fixture does not pretend to prove it.
