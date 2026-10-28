#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Import Tempest history from the verdify-platform database into weather-pg (#167).
#
#   scripts/import_verdify_history.sh FIRST_MONTH LAST_MONTH   (YYYY-MM, inclusive)
#
# One batch per month, streamed in-cluster: a read-only COPY out of verdify-db (owner role, read-only
# transaction), the tested transform in the weather app pod (weather.importer.verdify), and one
# transaction into weather-pg that inserts only rows before the first live report. Reruns insert
# nothing (ON CONFLICT DO NOTHING on source, family and source time). Rows are station reports
# (family obs_st) and the Tempest's rapid-wind samples (rapid_wind). The Mac only pipes the bytes; nothing is
# written to disk. Runbook: docs/operations/telemetry.md, "Imported history".
set -euo pipefail
CONTEXT=${KUBE_CONTEXT:-vallery}
PAUSE=${IMPORT_PAUSE_SECONDS:-20}
first=${1:?first month YYYY-MM}
last=${2:?last month YYYY-MM}
[[ $first =~ ^[0-9]{4}-[0-9]{2}$ && $last =~ ^[0-9]{4}-[0-9]{2}$ ]] || { echo "months are YYYY-MM" >&2; exit 2; }

src() { kubectl --context "$CONTEXT" -n verdify-prod "$@"; }
dst() { kubectl --context "$CONTEXT" -n weather "$@"; }
app=$(dst get pods -l app.kubernetes.io/name=api -o name | head -1)
[ -n "$app" ] || app=$(dst get pods -o name | grep '^pod/api-' | head -1)
primary=$(dst get pods -l cnpg.io/cluster=weather-pg,cnpg.io/instanceRole=primary -o name | head -1)
[ -n "$app" ] && [ -n "$primary" ] || { echo "weather app pod or weather-pg primary not found" >&2; exit 1; }

next_month() { python3 -c "import sys; y, m = map(int, sys.argv[1].split('-')); print(f'{y + m // 12:04d}-{m % 12 + 1:02d}')" "$1"; }

month=$first
while [[ $month < $(next_month "$last") ]]; do
  start="${month}-01T00:00:00Z"; end="$(next_month "$month")-01T00:00:00Z"
  export_sql=$(dst exec "$app" -- python -c "from weather.importer.verdify import EXPORT_SQL; print(EXPORT_SQL.format(start='$start', end='$end'))")
  batch=$(python3 -c "import uuid; print(uuid.uuid4())")
  # Two statements: the batch row first (its cutoff is the first live report now), then the rows.
  batch_sql="INSERT INTO weather.import_batch(id,source,source_start,source_end,cutoff)
    VALUES ('$batch','verdify-platform','$start','$end',
            coalesce((SELECT min(source_ts) FROM weather.obs_st),'infinity'::timestamptz))"
  load_sql="WITH c AS (SELECT cutoff FROM weather.import_batch WHERE id='$batch'),
    ins AS (
      INSERT INTO weather.obs_import(batch_id,source,family,source_ts,\"values\",cap_seconds)
      SELECT '$batch','verdify-platform',s.family,s.source_ts,s.\"values\",s.cap_seconds
      FROM staging s CROSS JOIN c WHERE s.source_ts < c.cutoff
      ON CONFLICT (source,family,source_ts) DO NOTHING RETURNING 1)
    UPDATE weather.import_batch i SET
      source_rows=(SELECT count(*) FROM staging),
      inserted_rows=(SELECT count(*) FROM ins),
      overlap_rows=(SELECT count(*) FROM staging s CROSS JOIN c WHERE s.source_ts >= c.cutoff),
      duplicate_rows=(SELECT count(*) FROM staging s CROSS JOIN c WHERE s.source_ts < c.cutoff)
                     - (SELECT count(*) FROM ins),
      finished_at=clock_timestamp(),state='complete'
    WHERE i.id='$batch'
    RETURNING i.source_start,i.source_rows,i.inserted_rows,i.overlap_rows,i.duplicate_rows"
  echo "== $month"
  src exec verdify-db-0 -c postgres -- sh -c 'psql -U "${POSTGRES_USER:-postgres}" -d verdify -X -q -v ON_ERROR_STOP=1 -c "SET default_transaction_read_only = on" -c "$1"' sh "$export_sql" \
    | dst exec -i "$app" -- python -m weather.importer.verdify transform \
    | dst exec -i "$primary" -c postgres -- psql -d weather -X -q -v ON_ERROR_STOP=1 -1 \
        -c "CREATE TEMP TABLE staging (family varchar(16) NOT NULL, source_ts timestamptz NOT NULL, \"values\" jsonb NOT NULL, cap_seconds integer NOT NULL)" \
        -c "COPY staging FROM STDIN WITH (FORMAT csv)" \
        -c "$batch_sql" \
        -c "$load_sql"
  month=$(next_month "$month")
  [[ $month < $(next_month "$last") ]] && sleep "$PAUSE"
done
echo "done"
