#!/bin/bash
# SPDX-License-Identifier: MIT
# weather#77 media drill: catalog copy. Restores the newest base backup of weather-pg from its
# B2 ObjectStore (weather-db-vallery/cnpg, reader weather-pg-backup-b2-reader referenced in
# place), replays the archived WAL there, promotes, then prepares the copy for the drill:
# pod-generated role passwords, TLS, and every media row outside the drill day marked
# unavailable so reconcile-objects checks only that day. Production is never contacted.
set -Eeuo pipefail
umask 077
export PGDATA=/pgdata/data PGHOST=/pgdata/run PGPORT=5432 PGUSER=postgres
mkdir -p "$PGHOST"
src=s3://weather-db-vallery/cnpg; ep="--endpoint-url https://s3.us-west-000.backblazeb2.com"
start=$(date -u +%s)
barman-cloud-backup-list $ep --format json "$src" weather-pg > /pgdata/backups.json
base=$(python3 -c 'import json;b=[x for x in json.load(open("/pgdata/backups.json"))["backups_list"] if x.get("status","DONE")=="DONE"];print(sorted(x["backup_id"] for x in b)[-1])')
echo "[catalog] restoring base $base"
barman-cloud-restore $ep "$src" weather-pg "$base" "$PGDATA"
chmod 0700 "$PGDATA"
sed -i -E 's/^(include|include_if_exists|include_dir)/#&/' "$PGDATA/postgresql.conf" || true
cat >> "$PGDATA/postgresql.auto.conf" <<CONF
restore_command = 'barman-cloud-wal-restore $ep $src weather-pg %f %p'
recovery_target_action = 'promote'
archive_mode = 'off'
hot_standby = 'off'
listen_addresses = ''
unix_socket_directories = '$PGHOST'
CONF
printf 'local all all trust\n' > "$PGDATA/pg_hba.conf"
touch "$PGDATA/recovery.signal"
pg_ctl -D "$PGDATA" -w -t 3000 start -l /pgdata/recovery.log >/dev/null
test "$(psql -d postgres -Atc 'select pg_is_in_recovery()')" = f
replay=$(psql -d postgres -Atc "select coalesce(extract(epoch from pg_last_xact_replay_timestamp())::bigint,0)")
q() { psql -d weather -v ON_ERROR_STOP=1 -Atc "$1"; }
for role in weather_api weather_worker weather_editor; do
  psql -d postgres -v ON_ERROR_STOP=1 -q -c "ALTER ROLE $role PASSWORD '$(cat /keys/db-$role/password)'"
done
day="$DRILL_DAY"
inday="(created_at AT TIME ZONE 'America/Denver')::date = DATE '$day'"
scoped=$(q "WITH u AS (UPDATE weather.media_asset SET state='unavailable' WHERE state='available' AND NOT ($inday) RETURNING 1) SELECT count(*) FROM u")
dayrows=$(q "SELECT count(*) FROM weather.media_asset WHERE state='available' AND $inday")
pg_ctl -D "$PGDATA" -m fast -w stop >/dev/null
cat >> "$PGDATA/postgresql.auto.conf" <<CONF
listen_addresses = '127.0.0.1'
port = 5432
ssl = on
ssl_cert_file = '/keys/tls/server.crt'
ssl_key_file = '/keys/pgkey/server.key'
CONF
printf 'local all all trust\nhostssl weather all 127.0.0.1/32 scram-sha-256\n' > "$PGDATA/pg_hba.conf"
printf '{"event":"weather_drill_catalog","base":"%s","replay_epoch":%s,"start_epoch":%s,"end_epoch":%s,"day":"%s","day_rows_available":%s,"rows_scoped_out":%s}\n' \
  "$base" "$replay" "$start" "$(date -u +%s)" "$day" "$dayrows" "$scoped" | tee /pgdata/catalog.json
