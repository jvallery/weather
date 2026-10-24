# SPDX-License-Identifier: MIT
"""Publication metrics. Labels are bounded: asset class, camera ID and fixed reason codes."""

from prometheus_client import Counter, Gauge

ATTEMPTS = Counter(
    "weather_publish_attempts_total", "Product publish attempts", ["product", "camera"]
)
FAILURES = Counter(
    "weather_publish_failures_total",
    "Failed product publish attempts by fixed reason",
    ["product", "camera", "reason"],
)
READBACK_FAILURES = Counter(
    "weather_publish_readback_failures_total",
    "Objects whose HEAD or GET-and-hash readback did not match the local hash",
    ["class"],
)
OBJECTS = Counter("weather_publish_objects_total", "Objects uploaded and read back", ["class"])
BYTES = Counter("weather_publish_bytes_total", "Bytes uploaded and read back", ["class"])
ORPHANS = Counter("weather_publish_orphans_total", "Upload intents resolved as orphans", ["reason"])
RECONCILED = Counter(
    "weather_publish_reconciled_total",
    "Reconciler actions: committed intents, restored sidecars, aborted multipart uploads",
    ["action"],
)
OLDEST_PENDING = Gauge(
    "weather_publish_oldest_pending_seconds",
    "Age of the oldest uncommitted upload intent (a ready but unpublished product)",
)
