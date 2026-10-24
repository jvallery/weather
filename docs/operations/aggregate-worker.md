# Closed aggregate revision worker

Source only: no pool, policy-file loading, scheduler activation or deployment is added here.
Root wires `AggregateRevisionWorker(...).register(registry)` with the existing worker engine and shared normalized public support binding; Changed routes scheduling, conflict acknowledges its atomic v4 Changed twin, and recompute handles closed windows.
Canonical UTC-hour and pinned America/Denver weather-day close jobs keep end+120-second due times and end+48-hour deadlines; content hashes bind station/window/full policy, while late keys additionally bind event watermark and keep the original event deadline on retry.
Explicit reported-backward/hold-forward caps bound affected windows; active windows are never retained, missing policies/authorities are skipped with fixed-reason metrics, and no rain/count support is inferred.
Recompute captures one committed watermark and evaluation instant, reads complete immutable evidence plus bookends/dependencies, reuses AggregateSource admission and overlap-cost checks, prepares outside the transaction, then persists revisions/heads and job completion through one existing owner/token fence.
`weather_aggregate_worker_skipped_total{reason}` covers uncommissioned, pressure/wind uncommissioned, expired and budget cases; exceptions remain generic. Focused pure checks pass; guarded PostgreSQL cases collect only, and cluster performance/PostgreSQL execution remain UNRUN.

The fleet-only cadence pilot seeds 1,500 minute observations and 30,000 three-second rapid-wind
observations across a closed 25-hour Denver day in the guarded disposable database. Explicit
reported-wind authority must preserve five full-day results despite the unrelated rapid-direction
rows. Seeding and recomputation each have a 120-second outer deadline; production input, work,
statement and prepared-output budgets remain unchanged. The registered validation runner bounds
the container at two CPUs and 4 GiB. Collection and lint are verified; actual execution and resource
behavior are not yet qualified. Never run this pilot on the Mac or against a production database.
