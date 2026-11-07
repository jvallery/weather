#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Aggregate CI gate: each argument is name=result (a GitHub Actions needs.<job>.result).
# Fails unless every listed shard job finished "success" (skipped/failure/cancelled fail).
set -euo pipefail
(( $# )) || { echo 'no shard results given' >&2; exit 2; }
failed=0
for pair in "$@"; do
  echo "$pair"
  if [[ "${pair#*=}" != success ]]; then
    echo "::error::CI shard ${pair%%=*} finished ${pair#*=}"
    failed=1
  fi
done
exit "$failed"
