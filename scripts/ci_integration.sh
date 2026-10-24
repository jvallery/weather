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
    pg_ctl -D "$wx_pg_root/data" -m immediate -w -t 20 stop >/dev/null 2>&1 || true
  fi
  rm -rf -- "$wx_pg_root"
}
trap cleanup EXIT INT TERM
mkdir "$wx_pg_root/socket"
initdb -D "$wx_pg_root/data" --username=postgres --auth-local=trust --auth-host=reject \
  --encoding=UTF8 --no-locale >/dev/null
pg_ctl -D "$wx_pg_root/data" -l "$wx_pg_root/server.log" -w -t 30 \
  -o "-k $wx_pg_root/socket -c listen_addresses='' -c max_connections=30 -c shared_buffers=64MB -c weather.integration_test=on" start >/dev/null
wx_pg_started=1
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
echo "PostgreSQL timezone package: $(dpkg-query -W -f='${Version}' tzdata)"
export WEATHER_CI_DATABASE_URL="postgresql+asyncpg://weather_migrator@/weather_ci?host=$wx_pg_root/socket"
export WEATHER_CI_ADMIN_DATABASE_URL="postgresql+asyncpg://postgres@/weather_ci?host=$wx_pg_root/socket"
uv run --frozen python -m alembic upgrade 0000_namespace
psql -h "$wx_pg_root/socket" -U weather_migrator -d weather_ci -v ON_ERROR_STOP=1 -q <<'SQL'
INSERT INTO weather.station(alias,site) VALUES('migration-seed','migration-seed');
SQL
uv run --frozen python -m alembic upgrade head
[[ "$(psql -h "$wx_pg_root/socket" -U weather_migrator -d weather_ci -Atqc "SELECT count(*) FROM weather.station WHERE alias='migration-seed'")" == 1 ]] || {
  echo 'Upgrade failed to preserve previous-head station row' >&2
  exit 1
}
uv run --frozen python -m alembic upgrade head
uv run --frozen python -m alembic check
uv run --frozen pytest -m integration
# Confirm terminal test success before removing all disposable server state.
pg_ctl -D "$wx_pg_root/data" -m fast -w -t 20 stop >/dev/null
wx_pg_started=0
echo 'PostgreSQL 16.15 / pgvector 0.8.6 integration completed; disposable server stopped'
