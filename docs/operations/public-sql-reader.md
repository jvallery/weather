# Public SQL reader source (WX-13)

This is an isolated source adaptor for the service protocol in `c70ffa0`, after
the database source in `71003ba`. It requires #9, #12 and migration
`0003_current_publication`; it does not compose their unmerged models into #9,
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

Eight focused pure tests cover numeric/clock/quality mapping, commissioned
expiry, support qualification, canonical positions, bound query parameters,
strict TLS configuration, pool bounds, empty-page shape and error sanitization.
They open no socket and execute no SQL. They were checked against exact service
source `c70ffa0138fe85383f31f33c4fe9dfca533ba738` through a temporary dependency
overlay; that service's files are not duplicated in this commit.

Three additional guarded integration cases are **unrun**: real API-role
current/observation reads including zero and large integer counts plus explicit
`obs_st` authority; a 30-day flattened export with late rows and same-transaction
conflict invalidation plus empty boundaries; and API pool saturation/statement
timeout while ingest commits independently and subsequent reads remain read-only.
They reuse the socket/version/marker guard and reference-seed cleanup from the
v3 migration tests. TLS negotiation against live CNPG remains a commissioning
check; the disposable socket fixture does not pretend to prove it.
