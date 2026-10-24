# Closed aggregate revision worker

Source only: no pool, policy-file loading, scheduler activation or deployment is added here.
Root wires `AggregateRevisionWorker(...).register(registry)` with the existing worker engine and shared normalized public support binding; Changed routes scheduling, conflict acknowledges its atomic v4 Changed twin, and recompute handles closed windows.
Canonical UTC-hour and pinned America/Denver weather-day close jobs keep end+120-second due times and end+48-hour deadlines; content hashes bind station/window/full policy, while late keys additionally bind event watermark and keep the original event deadline on retry.
Explicit reported-backward/hold-forward caps bound affected windows; active windows are never retained, missing policies/authorities are skipped with fixed-reason metrics, and no rain/count support is inferred.
Recompute captures one committed watermark and evaluation instant, reads complete immutable evidence plus bookends/dependencies, reuses AggregateSource admission and overlap-cost checks, prepares outside the transaction, then persists revisions/heads and job completion through one existing owner/token fence.
`weather_aggregate_worker_skipped_total{reason}` covers uncommissioned, pressure/wind uncommissioned, expired and budget cases; exceptions remain generic. Cluster source qualification passed 71 PostgreSQL integrations at `44ba876`, followed by the remaining full-cadence pilot at `2734a4b`. Production application code was identical between those heads. Normal final PR checks and live scheduler commissioning remain required; [the qualification record](https://github.com/jvallery/weather/issues/14#issuecomment-6385418712) preserves the exact source/Workflow identities and failed predecessors.

The fleet-only cadence pilot seeds 1,500 minute observations and 30,000 three-second rapid-wind
observations across a closed 25-hour Denver day in the guarded disposable database. Explicit
reported-wind authority must preserve five full-day results despite the unrelated rapid-direction
rows. Preparation commits batches of 128 rows, matching the spool drain bound, with all publication
triggers enabled. Its separate setup deadline is 300 seconds; recomputation retains its 120-second
deadline. Production input, work, statement and prepared-output budgets remain unchanged. The
registered validation runner bounds the container at two CPUs and 4 GiB. The actual pilot passed in
169.70 seconds including preparation, checked all five full-day results, and stopped its disposable
PostgreSQL server. Never run it on the Mac or against a production database.
