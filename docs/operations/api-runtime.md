# Measured-weather API runtime

The CLI uses the import-safe `weather.api:create_app` factory. With its default
`WEATHER_API_DATA_ENABLED=false`, only health/readiness routes are registered, and no policy file,
signing key or database credential is read. Explicit activation requires `WEATHER_API_STATION_ALIAS`
to match the public station alias stored by ingest. Do not enable it before CNPG migrations, the API
login/CA and the existing `weather-api-cursor` Secret are present.

The configured process reads `/etc/weather/api-policy.json` and the API-only read-only Secret mount
`/var/run/weather-api-cursor/CURSOR_HMAC_KEY`. The key must be the existing generated 64-character
URL-safe value; missing/invalid files fail startup with value-free diagnostics. Never create a
fallback key or put it in an environment variable, manifest, command or log. Database username,
password and CA come from `/var/run/weather-db`, through the bounded verify-full reader factory.

The public policy has the shape `{"version":1,"metrics":{}}`. Each commissioned metric entry has
exactly `expected_interval_seconds` and `hold_seconds`, both positive and bounded by one day when
specified. An explicit pair of nulls also represents an uncommissioned policy.
Duplicate/unknown keys, booleans masquerading as numbers and oversized files are rejected. An omitted
metric has no inferred freshness policy: persisted valid numbers stay visibly uncommissioned/stale.
Commission cadence and hold bounds from actual receiver evidence rather than HTTP poll time or
protocol report-interval guesses. Discovery and Current share those commissioned cadence values.
Policy changes alter discovery identities and signed cursor binding.

`WEATHER_API_RAW_WINDOW_SECONDS` defaults to 31 days, allows configuration from one minute through
366 days, and never changes a requested statistic implicitly. The reader still limits each page to
5,000 points, bounds statements to 1.5 seconds and connections to four with zero overflow. Two API
replicas use at most eight connections, or twelve during a three-pod rolling update, below the API
role's limit of forty. The source deadline is five seconds. Keep ingest and scheduler on their own
role/pool. The API pod's database-role label must be `api` for the existing database client policy.

Lifespan explicitly starts the shared five-second background refresh after source/key injection.
This keeps source-age metrics useful without public traffic. A database outage serves the original
cached measurements and advancing ages; a cold process returns sanitized 503. Health/readiness
remain process checks so a dependency outage does not discard a useful cache. Shutdown cancels the
refresh with a one-second bound and always disposes the pool under a separate three-second bound,
even if refresh cancellation fails. Metrics use the process registry and bounded labels; HTTP
access logs remain disabled to keep raw queries out of logs.

Current integration metadata composes immutable schema v1, telemetry v2 and publication v3.
Disposable integration cleanup preserves the camera seed, metric bindings and publication-clock
singleton; production tables are never truncated by these helpers. Source and pure startup/cache
tests do not prove real database permissions, export concurrency, saturation, outage recovery or
aggregate-revision handling. Those require the qualified in-cluster CI image and actual database
tests before WX-13 deployment. Aggregate reads remain unavailable until the full-window source and
retained revision worker are integrated.
