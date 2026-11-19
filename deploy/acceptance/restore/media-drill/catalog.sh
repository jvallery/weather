#!/bin/bash
# SPDX-License-Identifier: MIT
# weather#77 media drill: serve the prepared catalog copy on 127.0.0.1:5432 (TLS) only.
set -Eeuo pipefail
exec postgres -D /pgdata/data -k /pgdata/run
