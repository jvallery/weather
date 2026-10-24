#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$script_dir/restore_drill.py" "$@"
