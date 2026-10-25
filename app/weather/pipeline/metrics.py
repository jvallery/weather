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
SCHEDULE_ANCHORED = Counter(
    "weather_schedule_anchored_total",
    "New cycles started at the frozen previous next dawn because dawn was recomputed",
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
STILL_SLOTS = Counter(
    "weather_stills_slots_total",
    "Hour slots finalized by selection state (on_target, off_target, missing)",
    ["camera", "state"],
)
STILL_ARCHIVED = Counter(
    "weather_stills_archived_total",
    "Originals archived and read back, with their derivatives and selection row",
    ["camera"],
)
STILL_PENDING = Gauge(
    "weather_stills_pending_slots",
    "Closed hour slots of the last 12 h without an active selection",
    ["camera"],
)
STILL_OLDEST = Gauge(
    "weather_stills_oldest_pending_seconds",
    "Seconds since the end of the oldest closed hour slot without a selection",
    ["camera"],
)
STILL_FAILURES = Counter(
    "weather_stills_failures_total",
    "Hourly failures by stage (select, bind, archive, catalog); the slot retries",
    ["camera", "stage"],
)
PRESSURE_PAUSED = Counter(
    "weather_pipeline_pressure_paused_total",
    "Segment loops that added no mezzanine block because staging is past the render stop (97%)",
    ["camera"],
)
RELEASE_INCIDENTS = Counter(
    "weather_pipeline_release_incidents_total",
    "Renderer refused to resume: durable blocks (or recipe) behind segment.mezzanine_through",
    ["camera"],
)
