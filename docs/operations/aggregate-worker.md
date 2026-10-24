# Closed aggregate revision worker

Source only: no pool, policy-file loading, scheduler activation or deployment is added here.
Root wires `AggregateRevisionWorker(...).register(registry)` with the existing worker engine and shared normalized public support binding; Changed routes scheduling, conflict acknowledges its atomic v4 Changed twin, and recompute handles closed windows.
Canonical UTC-hour and pinned America/Denver weather-day close jobs keep end+120-second due times and end+48-hour deadlines; content hashes bind station/window/full policy, while late keys additionally bind event watermark and keep the original event deadline on retry.
Explicit reported-backward/hold-forward caps bound affected windows; active windows are never retained, missing policies/authorities are skipped with fixed-reason metrics, and no rain/count support is inferred.
Recompute captures one committed watermark and evaluation instant, reads complete immutable evidence plus bookends/dependencies, reuses AggregateSource admission and overlap-cost checks, prepares outside the transaction, then persists revisions/heads and job completion through one existing owner/token fence.
`weather_aggregate_worker_skipped_total{reason}` covers uncommissioned, pressure/wind uncommissioned, expired and budget cases; exceptions remain generic. Twelve pure checks pass; four guarded PostgreSQL cases collect only, and cluster performance/PostgreSQL execution remain UNRUN.
