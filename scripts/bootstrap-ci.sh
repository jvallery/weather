#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
[[ "${WEATHER_CI_IN_CLUSTER:-}" == "1" ]] || { echo 'Bootstrap requires approved fleet CI' >&2; exit 2; }
cd "$(dirname "$0")/.."
python3 scripts/bootstrap_ci.py
export WEATHER_CI_TOOLS="${WEATHER_CI_TOOLS:-/tmp/weather-ci-tools}"
export PATH="$WEATHER_CI_TOOLS:$WEATHER_CI_TOOLS/node-v24.22.1-linux-x64/bin:$PATH"
if [[ "${UV_PYTHON:-}" == /usr/local/bin/python3 ]]; then
  [[ "$("$UV_PYTHON" -c 'import platform; print(platform.python_version())')" == 3.14.9 ]] || {
    echo 'Preinstalled CI Python must be exactly 3.14.9' >&2
    exit 2
  }
else
  export UV_PYTHON_INSTALL_DIR="$WEATHER_CI_TOOLS/python"
  uv python install 3.14.9
fi
npm install --global --prefix "$WEATHER_CI_TOOLS" pnpm@12.15.0 --ignore-scripts --no-audit --no-fund
export PATH="$WEATHER_CI_TOOLS/bin:$PATH"
uv sync --frozen
exec "$@"
