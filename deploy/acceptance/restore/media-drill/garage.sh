#!/bin/bash
# SPDX-License-Identifier: MIT
# weather#77 media drill: single-node Garage on emptyDir with pod-generated keys. Listens on
# 127.0.0.1 only; buckets weather-media and weather-originals; one imported drill key.
set -Eeuo pipefail
mkdir -p /garage/meta /garage/data
cat > /garage/garage.toml <<CONF
metadata_dir = "/garage/meta"
data_dir = "/garage/data"
db_engine = "sqlite"
replication_factor = 1
rpc_bind_addr = "127.0.0.1:3901"
rpc_public_addr = "127.0.0.1:3901"
rpc_secret_file = "/keys/garage-server/rpc_secret"
[s3_api]
s3_region = "garage"
api_bind_addr = "127.0.0.1:3900"
[admin]
api_bind_addr = "127.0.0.1:3903"
admin_token_file = "/keys/garage-server/admin_token"
CONF
export GARAGE_CONFIG_FILE=/garage/garage.toml
/garage server &
pid=$!
for _ in $(seq 1 60); do /garage status >/dev/null 2>&1 && break; sleep 1; done
node=$(/garage node id -q 2>/dev/null | cut -d@ -f1)
/garage layout assign -z drill -c 20G "$node" >/dev/null
/garage layout apply --version 1 >/dev/null
/garage key import --yes -n drill "$(cat /keys/garage/weather-media/access_key_id)" "$(cat /keys/garage/weather-media/secret_access_key)" >/dev/null
for b in weather-media weather-originals; do
  /garage bucket create "$b" >/dev/null
  /garage bucket allow --read --write --owner "$b" --key drill >/dev/null
done
touch /garage/ready
echo "[garage] ready"
wait "$pid"
