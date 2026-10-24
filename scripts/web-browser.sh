#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
[[ "${WEATHER_CI_IN_CLUSTER:-}" == 1 && "$(uname -s)" == Linux ]] || { echo 'Browser qualification requires approved in-cluster CI' >&2; exit 2; }
cd "$(dirname "$0")/.."
pnpm --dir web install --frozen-lockfile --ignore-scripts
bash scripts/bootstrap-browser.sh
(cd web && pnpm build)
node scripts/check-web-build.mjs
result=0
(cd web && pnpm test:browser) || result=$?
node scripts/web-browser-report.mjs "$result"
exit "$result"
