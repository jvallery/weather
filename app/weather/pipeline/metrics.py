# SPDX-License-Identifier: MIT
"""Pipeline metrics: latency, gaps, coverage and failures. Labels are fixed vocabularies."""

from prometheus_client import Counter, Gauge, Histogram

FRAMES = Counter(
    "weather_pipeline_output_frames_total",
    "Output frames written to a mezzanine, by result",
    ["camera", "result"],
)
REJECTS = Counter(
    "weather_pipeline_slot_rejects_total",
    "Captured frames rejected per slot (decode, timing, geometry, settings, stale, changed)",
    ["camera", "reason"],
)
LATE = Counter(
    "weather_pipeline_late_frames_total",
    "Frames sealed after their block was already durable; not integrated",
    ["camera"],
)
LAG = Gauge(
    "weather_pipeline_render_lag_seconds",
    "Wall time minus the source time through which the mezzanine is durable",
    ["camera"],
)
COVERAGE = Gauge(
    "weather_pipeline_slot_coverage_ratio",
    "Slot coverage of the camera's most recently finalized segment",
    ["camera"],
)
SEGMENTS = Counter(
    "weather_pipeline_segments_total",
    "Finalized segments by catalog state",
    ["camera", "state"],
)
LATENCY = Histogram(
    "weather_pipeline_publish_latency_seconds",
    "Segment boundary to committed publication",
    ["camera"],
    buckets=(300, 600, 1200, 1800, 2700, 3600, 5400, 7200, 10800),
)
FAILURES = Counter(
    "weather_pipeline_failures_total",
    "Pipeline failures by stage; the segment retries on the next loop",
    ["camera", "stage"],
)
SCHEDULE_FAILURES = Counter(
    "weather_schedule_failures_total",
    "Schedule jobs that could not freeze cycles (site, discontinuity, solar)",
    ["reason"],
)
SITE_CONFIGURED = Gauge(
    "weather_schedule_site_configured",
    "1 when the private site configuration loaded on the last schedule attempt",
)
DEADLINES = Counter(
    "weather_segments_deadline_missed_total",
    "Segments still unpublished two hours after their boundary",
    ["camera"],
)
