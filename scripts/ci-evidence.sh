#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${WEATHER_CI_IN_CLUSTER:-}" != 1 || "$(uname -s)" != Linux ||
      -z "${KUBERNETES_SERVICE_HOST:-}" ]]; then
  echo 'Supply-chain image evidence requires the approved in-cluster CI profile' >&2
  exit 2
fi
command -v syft >/dev/null
uv run --frozen python scripts/ci_evidence.py
