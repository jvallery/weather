# SPDX-License-Identifier: MIT
"""Independent scrape/health server; fixed metric labels and no request logging."""

import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from .drain import Drain
from .parse import FAMILIES
from .receiver import Receiver

REJECTIONS = ("wrong_interface", "wrong_source", "wrong_identity", "malformed", "oversized")


class Metrics:
    def __init__(self, receiver: Receiver, drain: Drain) -> None:
        self.receiver, self.drain = receiver, drain
        self.registry = CollectorRegistry()
        self.registry.register(self)

    def collect(self) -> list[Any]:
        counts = dict(self.receiver.counts) | dict(self.drain.counts)
        result: list[Any] = []
        for name in (
            "received",
            "spooled",
            "committed",
            "projected",
            "unprojected",
            "quarantined",
            "drain_quarantined",
            "capacity_loss",
            "kernel_overflow",
            "spool_write_uncertain",
            "drain_errors",
            "spool_ack_errors",
        ):
            metric = CounterMetricFamily("weather_telemetry_" + name, "Telemetry " + name)
            metric.add_metric([], counts.get(name, 0))
            result.append(metric)
        rejected = CounterMetricFamily(
            "weather_telemetry_rejected", "Rejected datagrams", labels=["reason"]
        )
        for reason in REJECTIONS:
            rejected.add_metric([reason], counts.get("rejected_" + reason, 0))
        result.append(rejected)
        ages = GaugeMetricFamily(
            "weather_telemetry_last_packet_age_seconds",
            "Original receive age; absent family has no sample",
            labels=["family"],
        )
        now = time.time_ns()
        for family in sorted(FAMILIES):
            age = self.receiver.packet_age(family, now)
            if age is not None:
                ages.add_metric([family], max(0, float(age)))
        result.append(ages)
        stats = self.receiver.spool.stats()
        for name, key, help_text in (
            ("spool_checkpoint_errors", "checkpoint_errors", "Post-commit checkpoint errors"),
            ("spool_quarantined_rows", "quarantined_rows", "Undecodable spool rows set aside"),
            (
                "spool_unreadable_generations",
                "unreadable_generations",
                "Unreadable spool databases set aside",
            ),
        ):
            counter = CounterMetricFamily("weather_telemetry_" + name, help_text)
            counter.add_metric([], stats[key])
            result.append(counter)
        for suffix, key in (
            ("spool_depth", "depth"),
            ("spool_bytes", "pending_bytes"),
            ("spool_physical_bytes", "physical_bytes"),
            ("spool_capacity_bytes", "capacity_bytes"),
        ):
            gauge = GaugeMetricFamily("weather_telemetry_" + suffix, "Durable spool " + suffix)
            gauge.add_metric([], stats[key])
            result.append(gauge)
        lag = GaugeMetricFamily(
            "weather_telemetry_drain_lag_seconds", "Oldest original receipt age"
        )
        # Read-only: a scrape never decodes, quarantines or otherwise mutates the spool.
        oldest = self.receiver.spool.oldest_received_ns()
        lag.add_metric([], max(0, (now - oldest) / 1e9) if oldest is not None else 0)
        result.append(lag)
        return result

    def serve(self, port: int, receiver_thread: Thread) -> ThreadingHTTPServer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path in {"/live", "/ready"}:
                    healthy = receiver_thread.is_alive()
                    if self.path == "/ready":
                        healthy = healthy and owner.receiver.spool.stats()["pressure"] != "critical"
                    self.send_response(200 if healthy else 503)
                    self.end_headers()
                    self.wfile.write(b"ok\n" if healthy else b"unavailable\n")
                elif self.path == "/metrics":
                    try:
                        body = generate_latest(owner.registry)
                    except Exception:
                        self.send_response(503)
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", CONTENT_TYPE_LATEST)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                pass

        class QuietServer(ThreadingHTTPServer):
            def handle_error(self, request: Any, client_address: Any) -> None:
                # socketserver's default traceback includes the client's private address.
                pass

        server = QuietServer(("0.0.0.0", port), Handler)
        Thread(target=server.serve_forever, daemon=True, name="telemetry-metrics").start()
        return server
