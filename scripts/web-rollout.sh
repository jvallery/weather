#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
[[ "${WEATHER_CI_IN_CLUSTER:-}" == 1 && "$(uname -s)" == Linux ]] || { echo 'Rolling-browser proof requires approved in-cluster fleet CI' >&2; exit 2; }
cd "$(dirname "$0")/.."
pnpm --dir web install --frozen-lockfile --ignore-scripts
scripts/bootstrap-browser.sh --browsers-only
exec pnpm --dir web exec node e2e/rollout.mjs "$@"
