#!/bin/bash
# SPDX-License-Identifier: MIT
# weather#77 media drill: single-node Garage on emptyDir with pod-generated keys. Listens on
# 127.0.0.1 only; buckets weather-media and weather-originals; one imported drill key.
set -Eeuo pipefail
mkdir -p /gdata/meta /gdata/data /gdata/secrets
# Garage refuses group-readable secret files; keep 0600 copies owned by this user.
for f in rpc_secret admin_token; do
  install -m 0600 /keys/garage-server/$f /gdata/secrets/$f
done
cat > /gdata/garage.toml <<CONF
metadata_dir = "/gdata/meta"
data_dir = "/gdata/data"
db_engine = "sqlite"
replication_factor = 1
rpc_bind_addr = "127.0.0.1:3901"
rpc_public_addr = "127.0.0.1:3901"
rpc_secret_file = "/gdata/secrets/rpc_secret"
[s3_api]
s3_region = "garage"
api_bind_addr = "127.0.0.1:3900"
[admin]
api_bind_addr = "127.0.0.1:3903"
admin_token_file = "/gdata/secrets/admin_token"
CONF
export GARAGE_CONFIG_FILE=/gdata/garage.toml
/garage server &
pid=$!
for _ in $(seq 1 60); do
  kill -0 "$pid" 2>/dev/null || { echo "[garage] server exited" >&2; exit 1; }
  /garage status >/dev/null 2>&1 && break
  sleep 1
done
node=$(/garage node id -q 2>/dev/null | cut -d@ -f1)
/garage layout assign -z drill -c 20G "$node" >/dev/null
/garage layout apply --version 1 >/dev/null
/garage key import --yes -n drill "$(cat /keys/garage/weather-media/access_key_id)" "$(cat /keys/garage/weather-media/secret_access_key)" >/dev/null
for b in weather-media weather-originals; do
  /garage bucket create "$b" >/dev/null
  /garage bucket allow --read --write --owner "$b" --key drill >/dev/null
done
touch /gdata/ready
echo "[garage] ready"
wait "$pid"
