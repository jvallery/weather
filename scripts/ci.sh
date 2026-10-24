#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_PYTHON_DOWNLOADS=never
if (( $# == 0 )); then set -- all; fi
while (( $# )); do
  mode="$1"; shift
  case "$mode" in
    lint)
      uv lock --check
      export_tmp="$(mktemp)"
      trap 'rm -f "$export_tmp"' EXIT
      uv export --frozen --no-dev --no-emit-project --no-header --no-annotate --quiet > "$export_tmp"
      cmp requirements.lock "$export_tmp"
      rm -f "$export_tmp"
      trap - EXIT
      uv run --frozen ruff check app scripts
      uv run --frozen python scripts/lint_repository.py
      (cd web && pnpm install --frozen-lockfile --ignore-scripts && pnpm lint)
      ;;
    typecheck)
      uv run --frozen mypy
      (cd web && pnpm typecheck)
      ;;
    unit)
      uv run --frozen pytest -m 'not integration' \
        --cov=weather.timemodel --cov-report=term-missing --cov-fail-under=95
      (cd web && pnpm test)
      ;;
    integration)
      [[ "${WEATHER_CI_IN_CLUSTER:-}" == "1" ]] || { echo 'Integration requires fleet CI in-cluster' >&2; exit 2; }
      bash scripts/ci_integration.sh
      ;;
    supply-chain)
      gitleaks dir --redact --no-banner --config .gitleaks.toml .
      if [[ "${1:-}" == "--license-manifest" ]]; then
        shift; fixture="$1"; shift
        uv run --frozen python scripts/license_inventory.py --manifest "$fixture"
      else
        uv run --frozen python scripts/license_inventory.py
      fi
      if [[ "${1:-}" == "--image" ]]; then
        [[ "${WEATHER_CI_IN_CLUSTER:-}" == "1" ]] || { echo 'Image SBOM requires fleet CI in-cluster' >&2; exit 2; }
        shift; image="$1"; shift
        mkdir -p artifacts
        image_name="${image##*/}"
        image_name="${image_name%%[@:]*}"
        [[ "$image_name" =~ ^[A-Za-z0-9._-]+$ ]] || exit 2
        image_name="${image_name#weather-}"
        syft "registry:$image" -o "cyclonedx-json=artifacts/sbom-$image_name.json"
        sha256sum "artifacts/sbom-$image_name.json" > "artifacts/sbom-$image_name.json.sha256"
      fi
      ;;
    all)
      scripts/ci.sh lint typecheck unit integration supply-chain
      ;;
    *) echo "Unknown CI mode: $mode" >&2; exit 2 ;;
  esac
done
