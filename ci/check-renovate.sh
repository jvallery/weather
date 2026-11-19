#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Finite credential-free PR evidence on the existing Weather manifests job.
set -euo pipefail
[[ "${GITHUB_EVENT_NAME:-}" == pull_request ]]
[[ "$(uname -s)-$(uname -m)" == Linux-x86_64 ]]
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp"/{home,node,runtime,pnpm,candidate/web}
# Freeze the exact owner configuration, direct package pins and pnpm lock.
printf '%s  %s\n' \
  a5a98f605d1c8bfd63f3f8c41d4e4198e632d16623d62894a2548c16a92d1d13 renovate.json \
  6e206bb06d10924ec72a1a2ff05343cac043af50011c66c9c2ceb212621bd34b web/package.json \
  62b0e6f1f6dd5b995edc7e9f6ca45a5ed7eeb722d70d57e475e86defa3106e59 web/pnpm-lock.yaml | sha256sum --check
cp renovate.json "$tmp/candidate/"
cp web/{package.json,pnpm-lock.yaml} "$tmp/candidate/web/"
clean_env=(env -i "PATH=$PATH" "HOME=$tmp/home" CI=true)
download() {
  local url="$1" destination="$2" sha256="$3"
  "${clean_env[@]}" curl --fail --silent --show-error --max-time 30 \
    --proto '=https' --tlsv1.2 "$url" --output "$destination"
  printf '%s  %s\n' "$sha256" "$destination" | sha256sum --check --status
}

version=44.200.0
source=https://raw.githubusercontent.com/renovatebot/renovate/44.200.0
registry=https://registry.npmjs.org
download "https://nodejs.org/dist/v24.22.1/node-v24.22.1-linux-x64.tar.gz" "$tmp/node.tgz" \
  eb6728e77c576a35162c122c45ccadf224d89ff00abc8dae46cc2897c6002d7c
tar -xzf "$tmp/node.tgz" -C "$tmp/node" --strip-components=1
clean_env=(env -i "PATH=$tmp/node/bin:$PATH" "HOME=$tmp/home" CI=true)
download "$source/package.json" "$tmp/runtime/package.json" \
  3beff42005eeb0705c241e614fc7bf390d4005d7cba2f123867bdfa4f95853d8
download "$source/pnpm-lock.yaml" "$tmp/runtime/pnpm-lock.yaml" \
  6b2805b02a481d97e7dfe22647a6672806ccd0d2b77f48fe60dc324ff9815a9b
download "$source/pnpm-workspace.yaml" "$tmp/runtime/pnpm-workspace.yaml" \
  6074508f2b3f2d505be28c3a935f0d1ee9cd9a7d2a82fde107aab5b8955a3275
download "$registry/pnpm/-/pnpm-11.28.8.tgz" "$tmp/pnpm.tgz" \
  02bf6cec47fe28f30a224204f924175c2d8750c60e84e315a25cba57ce84da6a
download "$registry/renovate/-/renovate-$version.tgz" "$tmp/renovate.tgz" \
  d94ba216205650fe2d917508a59fca9f3489f932c893d4e11c822987e5c8a1d3
tar -xzf "$tmp/pnpm.tgz" -C "$tmp/pnpm" --strip-components=1
tar -xzf "$tmp/renovate.tgz" -C "$tmp/runtime" --strip-components=1 package/dist

(
  cd "$tmp/runtime"
  "${clean_env[@]}" timeout --kill-after=10s 180s node "$tmp/pnpm/bin/pnpm.cjs" \
    install --prod --frozen-lockfile --ignore-scripts --registry="$registry"
)
# The source manifest matches the lock. The released manifest identifies the built CLI.
tar -xzf "$tmp/renovate.tgz" -C "$tmp/runtime" --strip-components=1 package/package.json

(
  cd "$tmp/candidate"
  # No native addon builds. This candidate uses fixed matcher strings.
  runtime_env=("${clean_env[@]}" RENOVATE_X_IGNORE_RE2=1 "RENOVATE_BASE_DIR=$tmp/home/renovate")
  actual_version=$("${runtime_env[@]}" timeout --kill-after=5s 30s node "$tmp/runtime/dist/renovate.js" --version)
  [[ "$actual_version" == "$version" ]]
  printf 'Renovate %s\n' "$actual_version"
  "${runtime_env[@]}" timeout --kill-after=5s 60s node "$tmp/runtime/dist/config-validator.js" \
    --no-global --strict renovate.json
  "${runtime_env[@]}" LOG_LEVEL=trace LOG_FORMAT=json timeout --kill-after=5s 60s \
    node "$tmp/runtime/dist/renovate.js" --platform=local --dry-run=extract > "$tmp/extract.jsonl" || {
      echo "Native extraction failed" >&2
      exit 1
    }
)


"${clean_env[@]}" RENOVATE_X_IGNORE_RE2=1 timeout --kill-after=5s 60s node ci/check-renovate.mjs \
  "$tmp/runtime/dist" "$tmp/candidate" "$tmp/extract.jsonl"
