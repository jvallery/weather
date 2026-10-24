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

It also reads `/etc/weather/aggregate-policy.json`, with `version:1`, a `metrics` object and optional
`pressure` endpoint policy. Each metric uses an explicit versioned `forward_hold` or approved
`reported_interval` policy. Amounts/counts require one declared source authority. The shared loader
rejects unknown fields, unqualified reported support and implicit defaults, preserves exact decimal
caps, and emits the same normalized public policy document for worker persistence. An empty metrics
object makes no support claim. Include both configuration files before opting into data routes.

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

Current integration metadata composes immutable schema v1, telemetry v2, publication v3,
qualification history v4 and aggregate revisions v5.
Disposable integration cleanup preserves the camera seed, metric bindings and publication-clock
singleton; production tables are never truncated by these helpers. Source and pure startup/cache
tests do not prove real database permissions, export concurrency, saturation, outage recovery or
aggregate-revision handling. Those require the qualified in-cluster CI image and actual database
tests before WX-13 deployment. Aggregate requests now use the complete as-of source and bounded
calculator, with one nonwaiting process slot, cooperative work accounting and explicit overflow;
they do not silently truncate inputs or change the statistic. Their signed continuation position
binds the fixed UTC evaluation instant as well as the ordinal, and the publication watermark retains
value/qualification membership. Retained closed-window revision-worker commissioning remains pending.

## Disabled delivery component

`deploy/k8s/api-data` is excluded from the root kustomization. Enabling that component later adds
the hashed public policy ConfigMap, API-only database/CA/cursor mounts and database-role label, and
one `Recreate` scheduler. API and scheduler sync at wave 2, after the wave-1 migration Job; the
scheduler cannot overlap old/new calibration during a policy rollout. ConfigMap content hashes
change both pod templates when policy files change. The prepared files intentionally contain empty
uncommissioned metrics. Set measured policy values and verify the public alias `Longmont` against
ingest before enabling the component.

Commission only after accepted code/images, all required Secrets, migrations and real cluster
database/browser checks are ready. Add `components: [api-data]` to the existing root kustomization
in the digest-promotion change; do not hand-apply its patches or mount the cursor into another role.
The current root render remains the health-only API/web skeleton. Component render/volume-ref and
sync-wave checks have passed locally; nothing was applied to the cluster by those checks.
