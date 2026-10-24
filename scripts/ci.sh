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
      uv run --frozen pytest -m 'not integration'
      (cd web && pnpm test)
      ;;
    integration)
      [[ "${WEATHER_CI_IN_CLUSTER:-}" == "1" ]] || { echo 'Integration requires fleet CI in-cluster' >&2; exit 2; }
      # No integration suite exists in WX-02. Later issues add service-backed tests here.
      if find app/tests -name 'test_*integration*.py' -print -quit | read -r _; then
        uv run --frozen pytest -m integration
      fi
      ;;
    supply-chain)
      gitleaks dir --redact --no-banner --config .gitleaks.toml .
      uv run --frozen python scripts/license_inventory.py
      if [[ "${1:-}" == "--image" ]]; then
        [[ "${WEATHER_CI_IN_CLUSTER:-}" == "1" ]] || { echo 'Image SBOM requires fleet CI in-cluster' >&2; exit 2; }
        shift; image="$1"; shift
        mkdir -p artifacts
        syft "$image" -o cyclonedx-json=artifacts/image.cdx.json
      fi
      ;;
    all)
      scripts/ci.sh lint typecheck unit integration supply-chain
      ;;
    *) echo "Unknown CI mode: $mode" >&2; exit 2 ;;
  esac
done
