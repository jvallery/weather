#!/bin/bash
# SPDX-License-Identifier: MIT
# weather#77 media drill: pod-local secrets. Every value is generated here, lives only in
# the pod's emptyDir and dies with the pod. Nothing is printed.
set -Eeuo pipefail
umask 027
K=/keys
mkdir -p "$K/tls" "$K/pgkey" "$K/garage-server" "$K/cursor" \
  "$K/garage/weather-media" "$K/garage/weather-originals"
for role in weather_api weather_worker weather_editor; do
  mkdir -p "$K/db-$role"
  printf '%s' "$role" > "$K/db-$role/username"
  openssl rand -hex 24 > "$K/db-$role/password"
done
# Throwaway CA and a server certificate for the name the app dials (weather-pg-rw); the pod's
# hostAliases pin that name to 127.0.0.1.
# Extensions satisfy Python's VERIFY_X509_STRICT (the app's default TLS context).
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj '/CN=weather-media-drill-ca' \
  -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign' \
  -addext 'subjectKeyIdentifier=hash' -keyout "$K/tls/ca.key" -out "$K/tls/ca.crt" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj '/CN=weather-pg-rw' \
  -keyout "$K/pgkey/server.key" -out "$K/tls/server.csr" 2>/dev/null
printf '%s\n' 'subjectAltName=DNS:weather-pg-rw,IP:127.0.0.1' 'basicConstraints=critical,CA:FALSE' \
  'keyUsage=critical,digitalSignature,keyEncipherment' 'extendedKeyUsage=serverAuth' \
  'authorityKeyIdentifier=keyid,issuer' 'subjectKeyIdentifier=hash' > "$K/tls/san.ext"
openssl x509 -req -in "$K/tls/server.csr" -CA "$K/tls/ca.crt" -CAkey "$K/tls/ca.key" \
  -CAcreateserial -days 2 -extfile "$K/tls/san.ext" -out "$K/tls/server.crt" 2>/dev/null
rm -f "$K/tls/ca.key" "$K/tls/server.csr"
chmod 0600 "$K/pgkey/server.key"
for role in weather_api weather_worker weather_editor; do cp "$K/tls/ca.crt" "$K/db-$role/ca.crt"; done
openssl rand -hex 32 > "$K/garage-server/rpc_secret"
openssl rand -hex 32 > "$K/garage-server/admin_token"
openssl rand -hex 32 > "$K/cursor/CURSOR_HMAC_KEY"
# One drill S3 key (Garage format: GK + 24 hex, 64-hex secret), shared by both buckets.
id="GK$(openssl rand -hex 12)"; secret="$(openssl rand -hex 32)"
for b in weather-media weather-originals; do
  printf '%s' "$id" > "$K/garage/$b/access_key_id"
  printf '%s' "$secret" > "$K/garage/$b/secret_access_key"
done
find "$K" -mindepth 1 -exec chmod g+rX {} +
chmod 0700 "$K/pgkey"
chmod 0600 "$K/pgkey/server.key"
echo "[keys] generated"
