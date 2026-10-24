#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# The fleet validation container owns this entire disposable PostgreSQL instance.
set -euo pipefail
[[ "${WEATHER_CI_IN_CLUSTER:-}" == "1" && "$(uname -s)" == Linux && "$(id -u)" != 0 ]] || {
  echo 'Database integration requires approved non-root in-cluster CI' >&2
  exit 2
}
cd "$(dirname "$0")/.."
postgres --version | grep -F 'PostgreSQL) 16.15' >/dev/null || {
  echo 'Integration requires the production PostgreSQL 16.15 image' >&2
  exit 2
}
wx_pg_root="$(mktemp -d "${TMPDIR:-/tmp}/weather-schema-ci.XXXXXX")"
wx_pg_started=0
cleanup() {
  if [[ "$wx_pg_started" == 1 ]]; then
    if ! pg_ctl -D "$wx_pg_root/data" -m immediate -w -t 20 stop >/dev/null 2>&1; then
      if pg_ctl -D "$wx_pg_root/data" status >/dev/null 2>&1; then
        echo 'Disposable PostgreSQL still running; preserving its temporary directory' >&2
        return 1
      fi
    fi
  fi
  rm -rf -- "$wx_pg_root"
}
trap 'wx_exit_status=$?; cleanup || wx_exit_status=1; exit "$wx_exit_status"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir "$wx_pg_root/socket"
initdb -D "$wx_pg_root/data" --username=postgres --auth-local=trust --auth-host=reject \
  --encoding=UTF8 --no-locale >/dev/null
wx_pg_started=1
pg_ctl -D "$wx_pg_root/data" -l "$wx_pg_root/server.log" -w -t 30 \
  -o "-k $wx_pg_root/socket -c listen_addresses='' -c max_connections=30 -c shared_buffers=64MB -c weather.integration_test=on" start >/dev/null
createdb -h "$wx_pg_root/socket" -U postgres weather_ci
psql -h "$wx_pg_root/socket" -U postgres -d weather_ci -v ON_ERROR_STOP=1 -q <<'SQL'
CREATE ROLE weather_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE weather_ingest LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE weather_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE weather_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE weather_editor LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE EXTENSION vector;
ALTER DATABASE weather_ci OWNER TO weather_migrator;
SQL
wx_vector="$(psql -h "$wx_pg_root/socket" -U postgres -d weather_ci -Atqc "SELECT extversion FROM pg_extension WHERE extname='vector'")"
[[ "$wx_vector" == 0.8.6 ]] || { echo 'Integration requires production pgvector 0.8.6' >&2; exit 2; }
psql -h "$wx_pg_root/socket" -U postgres -d weather_ci -v ON_ERROR_STOP=1 \
  -c "SELECT version() AS postgres_version, (SELECT extversion FROM pg_extension WHERE extname='vector') AS vector_version"
createdb -h "$wx_pg_root/socket" -U postgres weather_ci_empty
# Production already holds the restore-drill sentinel schema; the baseline must ignore it.
psql -h "$wx_pg_root/socket" -U postgres -d weather_ci_empty -v ON_ERROR_STOP=1 -q <<'SQL'
CREATE EXTENSION vector;
ALTER DATABASE weather_ci_empty OWNER TO weather_migrator;
CREATE SCHEMA weather_recovery_drill;
CREATE TABLE weather_recovery_drill.sentinel(operation text NOT NULL, sequence integer NOT NULL,
  PRIMARY KEY (operation, sequence));
INSERT INTO weather_recovery_drill.sentinel VALUES ('ci', 0);
SQL
export WEATHER_CI_DATABASE_URL="postgresql+asyncpg://weather_migrator@/weather_ci_empty?host=$wx_pg_root/socket"
uv run --frozen python -m alembic upgrade head
uv run --frozen python -m alembic check
uv run --frozen python -m alembic current --check-heads
[[ "$(psql -h "$wx_pg_root/socket" -U postgres -d weather_ci_empty -Atqc "SELECT count(*) FROM weather_recovery_drill.sentinel")" == 1 ]] || {
  echo 'Baseline touched the restore-drill sentinel schema' >&2
  exit 1
}
echo "Production PostgreSQL timezone package: $(cat /usr/share/doc/weather-ci/production-tzdata.txt)"
sha256sum -c /usr/share/doc/weather-ci/production-denver.sha256
echo "CI timezone package: $(dpkg-query -W -f='${Version}' tzdata)"
uv run --frozen python - <<'PY'
from weather.timemodel.site import IANA_VERSION, TZDATA_VERSION
print(f'Python timezone package: tzdata {TZDATA_VERSION}, IANA {IANA_VERSION}')
PY
export WEATHER_CI_DATABASE_URL="postgresql+asyncpg://weather_migrator@/weather_ci?host=$wx_pg_root/socket"
export WEATHER_CI_ADMIN_DATABASE_URL="postgresql+asyncpg://postgres@/weather_ci?host=$wx_pg_root/socket"
uv run --frozen python -m alembic upgrade head
# A repeated upgrade at head is a no-op.
uv run --frozen python -m alembic upgrade head
uv run --frozen python -m alembic check
uv run --frozen pytest -m integration
# Confirm terminal test success before removing all disposable server state.
pg_ctl -D "$wx_pg_root/data" -m fast -w -t 20 stop >/dev/null
wx_pg_started=0
echo 'PostgreSQL 16.15 / pgvector 0.8.6 integration completed; disposable server stopped'
