# SPDX-License-Identifier: MIT
"""Low-cardinality metrics and a liveness server independent of blocking NFS I/O."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from .chunks import IOHealth

RESULTS = (
    "captured",
    "timeout",
    "transport",
    "protocol",
    "authentication",
    "redirect",
    "http_status",
    "content_type",
    "oversized",
    "truncated",
    "jpeg_markers",
    "geometry",
    "decode_error",
    "overdue",
    "backoff",
    "staging_full",
    "persist_failed",
    "shutdown",
)


class CaptureMetrics:
    def __init__(self, camera: str, health: IOHealth) -> None:
        self.health, self.camera = health, camera
        self.registry = CollectorRegistry()
        self.last_good_ns: int | None = None
        self.attempts = {
            result: Counter(
                f"weather_capture_attempts_{result}_total",
                "Scheduled capture outcomes",
                ["camera"],
                registry=self.registry,
            ).labels(camera)
            for result in RESULTS
        }
        self.age = Gauge(
            "weather_capture_last_good_frame_age_seconds",
            "Age; -1 means no successful capture",
            ["camera"],
            registry=self.registry,
        ).labels(camera)
        self.duration = Histogram(
            "weather_capture_request_seconds",
            "Whole attempt elapsed time",
            ["camera"],
            buckets=(0.1, 0.5, 1, 2, 3, 5, 10),
            registry=self.registry,
        ).labels(camera)
        self.bytes = Histogram(
            "weather_capture_request_bytes",
            "Accepted native JPEG bytes",
            ["camera"],
            buckets=(65536, 524288, 1048576, 4194304, 8388608, 16777216),
            registry=self.registry,
        ).labels(camera)
        self.seal_latency = Histogram(
            "weather_capture_seal_seconds",
            "Chunk seal duration",
            ["camera"],
            registry=self.registry,
        ).labels(camera)
        self.abandoned = Counter(
            "weather_capture_abandoned_chunks_total",
            "Uncertain chunks left in tmp",
            ["camera"],
            registry=self.registry,
        ).labels(camera)
        self.seal_uncertain = Counter(
            "weather_capture_seal_uncertain_total",
            "Rename completed but directory sync failed",
            ["camera"],
            registry=self.registry,
        ).labels(camera)
        self.staging_ratio = Gauge(
            "weather_capture_staging_ratio",
            "Filesystem occupancy including unavailable blocks",
            ["camera"],
            registry=self.registry,
        ).labels(camera)

    def result(self, result: str, count: int = 1) -> None:
        self.attempts[result].inc(count)
        if result == "captured":
            self.last_good_ns = time.monotonic_ns()

    def render(self) -> bytes:
        self.age.set(
            (time.monotonic_ns() - self.last_good_ns) / 1e9 if self.last_good_ns is not None else -1
        )
        return generate_latest(self.registry)

    def serve(self, port: int) -> ThreadingHTTPServer:
        metrics = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/metrics":
                    body, status = metrics.render(), 200
                elif self.path in {"/livez", "/readyz"}:
                    status = 200 if metrics.health.healthy() else 503
                    body = b"ok\n" if status == 200 else b"filesystem operation overdue\n"
                else:
                    status, body = 404, b"not found\n"
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                # Even a deliberately malformed request must not enter logs.
                pass

        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
