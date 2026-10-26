# SPDX-License-Identifier: MIT
"""Absolute timer with one request, bounded pressure and explicit capture loss.

Every NFS operation (chunk append, seal, statvfs) runs on one ordered I/O thread (#145), so a
slow write or seal never delays the next request: only the camera request itself is on the
capture timer. Records reach the chunk in slot order; the backlog is bounded."""

import asyncio
import hashlib
import logging
import os
import re
import signal
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from weather.logs import configure, event

from .chunks import ChunkWriter, IOHealth, new_incarnation
from .client import CaptureFailure, SnapshotClient
from .config import CaptureConfig
from .jpeg import validate_jpeg
from .metrics import CaptureMetrics
from .timing import Backoff, Schedule, Slot, request_timing, utc_text

# Queued NFS operations before the timer waits for the I/O thread (about a minute at 0.5 Hz).
IO_BACKLOG = 32
# #147: one summary line per minute that lost slots; nothing while every slot is captured.
LOSS_WINDOW_NS = 60 * 1_000_000_000
LOG = logging.getLogger("weather.capture")


class Watermark:
    def __init__(self) -> None:
        self.stopped = False

    def update(self, ratio: float) -> bool:
        if ratio >= 0.90:
            self.stopped = True
        elif ratio < 0.75:
            self.stopped = False
        return self.stopped


class PendingLoss:
    """Constant-memory summary when the mount cannot retain individual attempts."""

    def __init__(self) -> None:
        self.record: dict[str, object] | None = None
        self.count = 0

    def add(self, record: dict[str, object]) -> None:
        increment = record.get("slot_count", 1)
        if not isinstance(increment, int) or increment < 1:
            raise ValueError("Invalid loss count")
        if self.record is None:
            self.count = 0
        self.count += increment
        if self.record is None:
            self.record = {
                "record_type": "loss_range",
                "failure_class": "persist_failed",
                "first_slot_id": record.get("slot_id"),
                "last_slot_id": record.get("slot_id"),
                "first_slot_at_utc": record.get("slot_at_utc"),
                "last_slot_at_utc": record.get("slot_at_utc"),
                "attempt_count": self.count,
                "individual_details_lost": True,
            }
        else:
            self.record["last_slot_id"] = record.get("last_slot_id", record.get("slot_id"))
            self.record["last_slot_at_utc"] = record.get(
                "last_slot_at_utc", record.get("slot_at_utc")
            )
            self.record["attempt_count"] = self.count


def unsealed_inventory(staging: Path, camera: str) -> list[dict[str, object]]:
    """Names only. Another incarnation may be alive: never classify its file as failed."""
    root = staging / "tmp" / camera
    records: list[dict[str, object]] = []
    overflow = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                match = re.fullmatch(r"([0-9]{1,20})_([0-9]{1,20})__(.+)\.tar", entry.name)
                if match is None:
                    continue
                if len(records) >= 128:
                    overflow += 1
                    continue
                start, end = int(match[1]), int(match[2])
                records.append(
                    {
                        "record_type": "unsealed_range",
                        "classification": "unsealed_uncertain",
                        "candidate_id": hashlib.sha256(entry.name.encode()).hexdigest(),
                        "start_at_utc": utc_text(start),
                        "end_at_utc": utc_text(end),
                        "individual_details_lost": True,
                        "reconciliation": (
                            "compare sealed chunks and accepted slots before assigning loss"
                        ),
                    }
                )
    except FileNotFoundError:
        return []
    if overflow:
        records.append(
            {
                "record_type": "unsealed_range",
                "classification": "unsealed_uncertain",
                "inventory_overflow_count": overflow,
                "individual_details_lost": True,
                "reconciliation": "scheduler must inventory remaining tmp candidates",
            }
        )
    return records


class Collector:
    def __init__(self, config: CaptureConfig) -> None:
        self.config = config
        self.health = IOHealth()
        self.metrics = CaptureMetrics(config.camera, self.health)
        self.client = SnapshotClient(config)
        self.incarnation = new_incarnation()
        self.sequence = 0
        self.writer: ChunkWriter | None = None
        self.pending = PendingLoss()
        self.uncertain: list[dict[str, object]] = []
        self.watermark = Watermark()
        self.backoff = Backoff()
        self.next_request_ns = 0
        # Storage failures back off separately, so a healthy camera cannot reset the delay.
        self.storage_backoff = Backoff()
        self.storage_retry_ns = 0
        self.last_hash: str | None = None
        self.stop = asyncio.Event()
        self.active: asyncio.Task[Any] | None = None
        self._chunk_records: list[dict[str, object]] = []
        # One ordered thread owns the writer and every NFS call while `run` is active.
        self.io = ThreadPoolExecutor(max_workers=1, thread_name_prefix="capture-io")
        self._io: deque[asyncio.Future[Any]] = deque()
        self._running = False
        self._sealing: ChunkWriter | None = None  # the writer whose seal is queued
        self.full = False  # last watermark verdict, refreshed by the I/O thread
        self._losses: dict[str, int] = {}
        self._losses_since = time.monotonic_ns()

    def shutdown(self) -> None:
        self.stop.set()
        if self.active is not None:
            self.active.cancel()

    def _base(self, slot: Slot) -> dict[str, object]:
        return {
            "record_type": "capture_attempt",
            "attempt_id": slot.slot_id,
            "slot_id": slot.slot_id,
            "slot_at_utc": utc_text(slot.scheduled_utc_ns),
            "clock_epoch": slot.clock_epoch,
            "camera": self.config.camera,
            "status": "skipped",
            "failure_class": None,
            "member": None,
        }

    def _persist(self, slot: Slot, record: dict[str, object], body: bytes | None = None) -> bool:
        if time.monotonic_ns() < self.storage_retry_ns:
            # Constant memory and no NFS calls while the share is failing.
            self.pending.add(record)
            self.metrics.result("persist_failed")
            return False
        try:
            hour = utc_text(slot.scheduled_utc_ns)[:13].replace(":", "") + "Z"
            if self.writer is not None and (
                self.writer.hour != hour
                or self.writer.clock_epoch != slot.clock_epoch
                or not self.writer.window_start_ns
                <= slot.scheduled_utc_ns
                < self.writer.window_end_ns
                or slot.scheduled_mono_ns - self.writer.started_mono_ns
                >= self.config.chunk_seconds * 1_000_000_000
            ):
                self._seal()
            if self.writer is None:
                self._open_writer(slot)
            assert self.writer is not None
            self.writer.append(record, body)
            # At most 301 records per <=300-second, >=1-second chunk. Payloads stay on NFS.
            self._chunk_records.append(record)
            return True
        except OSError:
            self._abandon()
            self.pending.add(record)
            self.metrics.result("persist_failed")
            return False

    def _open_writer(self, slot: Slot) -> None:
        self.writer = ChunkWriter(
            self.config,
            self.incarnation,
            self.sequence,
            slot,
            self.health,
        )
        self.sequence += 1
        if self.pending.record:
            self.writer.append(self.pending.record)
        for uncertain in self.uncertain:
            self.writer.append(uncertain)

    def _finish(self) -> None:
        if self.writer is None and (self.pending.record or self.uncertain):
            # A recovered mount can retain pending loss even when no next capture is due.
            self._open_writer(
                Slot(
                    f"{self.incarnation}-{self.sequence}-metadata",
                    time.monotonic_ns(),
                    time.time_ns(),
                    self.incarnation,
                )
            )
        self._seal()

    def _abandon(self) -> None:
        """Every storage failure: drop the chunk, keep its loss and delay the next NFS write."""
        delay = self.storage_backoff.failure()
        self.storage_retry_ns = time.monotonic_ns() + delay * 1_000_000_000
        writer = self.writer
        if writer is not None:
            writer.abandon()
            self.writer = None
            self.metrics.abandoned.inc()
        if writer is not None and writer.renamed:
            self.metrics.seal_uncertain.inc()
            self.uncertain.append(
                {
                    "record_type": "unsealed_range",
                    "classification": "seal_uncertain",
                    "candidate_id": hashlib.sha256(writer.identity.encode()).hexdigest(),
                    "individual_details_lost": False,
                    "reconciliation": "verify complete sealed chunk and accepted slots",
                }
            )
        else:
            for record in self._chunk_records:
                self.pending.add(record)
        self._chunk_records = []

    def _seal(self) -> None:
        if self.writer is None:
            return
        # Callers abandon on OSError, which also applies the storage backoff once.
        start = time.monotonic()
        self.writer.seal()
        self.writer = None
        self._chunk_records = []
        self.pending.record = None
        self.uncertain = []
        self.storage_backoff.success()
        self.metrics.seal_latency.observe(time.monotonic() - start)

    def _occupancy(self) -> float:
        with self.health.operation():
            stat = os.statvfs(self.config.staging)
        if stat.f_blocks <= 0:
            raise OSError("Staging capacity unavailable")
        return 1 - stat.f_bavail / stat.f_blocks

    def _staging_full(self) -> bool:
        ratio = self._occupancy()
        self.metrics.staging_ratio.set(ratio)
        before = self.watermark.stopped
        stopped = self.watermark.update(ratio)
        if stopped != before:
            name = "staging_stop" if stopped else "staging_resume"
            level = logging.WARNING if stopped else logging.INFO
            event(LOG, name, level, camera=self.config.camera, ratio=round(ratio, 4))
        return stopped

    def _count(self, result: str, count: int = 1) -> None:
        if result != "captured":
            self._losses[result] = self._losses.get(result, 0) + count

    def _summarize(self, now_ns: int) -> None:
        if now_ns - self._losses_since < LOSS_WINDOW_NS:
            return
        if self._losses:
            event(
                LOG,
                "capture_loss",
                logging.WARNING,
                camera=self.config.camera,
                window_seconds=round((now_ns - self._losses_since) / 1e9),
                **self._losses,
            )
        self._losses, self._losses_since = {}, now_ns

    async def _attempt(self, slot: Slot, schedule: Schedule) -> None:
        record = self._base(slot)
        body: bytes | None = None
        start_wall, start_mono = time.time_ns(), time.monotonic_ns()
        try:
            if start_mono < self.storage_retry_ns:
                record["failure_class"] = "backoff"
            elif self.full if self._running else self._staging_full():
                record["failure_class"] = "staging_full"
            elif start_mono < self.next_request_ns:
                record["failure_class"] = "backoff"
            else:
                record["status"] = "request_failed"
                start_wall, start_mono = time.time_ns(), time.monotonic_ns()
                response_end: tuple[int, int] | None = None
                try:
                    self.active = asyncio.create_task(self.client.fetch())
                    response = await self.active
                    response_end = time.time_ns(), time.monotonic_ns()
                    validate_jpeg(response.body, self.config.geometry, self.config.max_bytes)
                    body = response.body
                    digest = hashlib.sha256(body).hexdigest()
                    record.update(status="captured", duplicate_hash=digest == self.last_hash)
                    self.last_hash = digest
                    self.backoff.success()
                    self.metrics.bytes.observe(len(body))
                except CaptureFailure as error:
                    record.update(failure_class=error.reason, http_status=error.status)
                    delay = self.backoff.failure(error.reason == "authentication")
                    self.next_request_ns = time.monotonic_ns() + delay * 1_000_000_000
                except asyncio.CancelledError:
                    record["failure_class"] = "shutdown"
                finally:
                    self.active = None
                    end_wall, end_mono = response_end or (time.time_ns(), time.monotonic_ns())
                    record.update(
                        request_timing(
                            start_wall,
                            end_wall,
                            start_mono,
                            end_mono,
                            self.config.cache_allowance_ms,
                            self.config.calibration_revision,
                        )
                    )
                    self.metrics.duration.observe((end_mono - start_mono) / 1e9)
                    schedule.observe_clock(time.time_ns(), time.monotonic_ns())
            result = "captured" if body is not None else str(record["failure_class"])
            self._count(result)
        except OSError:
            self._abandon()
            self.pending.add(record)
            self.metrics.result("persist_failed")
            return
        await self._submit(self._store, slot, record, body, result)
        if not self._running:
            await self.flush()

    def _store(
        self, slot: Slot, record: dict[str, object], body: bytes | None, result: str
    ) -> None:
        """On the I/O thread: persist one attempt, count it, refresh the watermark."""
        try:
            if self._persist(slot, record, body):
                self.metrics.result(result)
        except OSError:
            self._abandon()
            self.pending.add(record)
            self.metrics.result("persist_failed")
        if not self._running or time.monotonic_ns() < self.storage_retry_ns:
            return  # no NFS call during storage backoff; direct callers check per attempt
        try:
            self.full = self._staging_full()
        except OSError:
            pass  # the next append fails and backs off; keep the last verdict

    def _persist_quietly(self, slot: Slot, record: dict[str, object]) -> None:
        try:
            self._persist(slot, record)
        except OSError:
            self._abandon()

    def _seal_or_abandon(self) -> None:
        try:
            self._seal()
        except OSError:
            self._abandon()

    async def _submit(self, operation: Callable[..., None], *args: Any) -> None:
        """Queue one NFS operation in order; wait only when the backlog is full."""
        loop = asyncio.get_running_loop()
        self._io.append(loop.run_in_executor(self.io, operation, *args))
        while self._io and (len(self._io) > IO_BACKLOG or self._io[0].done()):
            await self._io.popleft()

    def _reap(self) -> None:
        """Drop finished I/O, raising any unexpected error from the I/O thread."""
        while self._io and self._io[0].done():
            self._io.popleft().result()

    async def flush(self) -> None:
        while self._io:
            await self._io.popleft()

    async def run(self, max_attempts: int | None = None) -> None:
        schedule = Schedule(
            time.time_ns(),
            time.monotonic_ns(),
            self.config.cadence_ms,
            self.config.phase_ms,
        )
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.shutdown)
        with self.health.operation():
            self.uncertain = unsealed_inventory(self.config.staging, self.config.camera)
        try:
            self.full = self._staging_full()
        except OSError:
            self.full = False  # the first append fails and backs off
        self._running = True
        count = 0
        try:
            while not self.stop.is_set():
                deadline = schedule.next_mono_ns
                writer = self.writer
                if writer is self._sealing:
                    writer = None  # its seal is already queued
                if writer is not None:
                    deadline = min(
                        deadline,
                        writer.started_mono_ns + writer.window_end_ns - writer.window_start_ns,
                    )
                self._reap()
                self._summarize(time.monotonic_ns())
                wait = (deadline - time.monotonic_ns()) / 1e9
                if writer is None and self._io:
                    # Queued I/O may open a chunk whose seal deadline is earlier than the
                    # next slot: look again shortly instead of sleeping to the slot.
                    wait = min(wait, 0.1)
                if wait > 0:
                    try:
                        await asyncio.wait_for(self.stop.wait(), wait)
                        break
                    except TimeoutError:
                        pass
                writer = self.writer
                if (
                    writer is not None
                    and writer is not self._sealing
                    and time.monotonic_ns()
                    >= writer.started_mono_ns + writer.window_end_ns - writer.window_start_ns
                ):
                    self._sealing = writer
                    await self._submit(self._seal_or_abandon)
                if time.monotonic_ns() < schedule.next_mono_ns:
                    continue
                schedule.observe_clock(time.time_ns(), time.monotonic_ns())
                slot = schedule.take()
                await self._attempt(slot, schedule)
                count += 1
                if max_attempts is not None and count >= max_attempts:
                    break
                # Skip every slot overrun by the complete request/validation/persistence operation.
                skipped_count = schedule.overdue_count(time.monotonic_ns())
                if skipped_count:
                    first, last = schedule.skip(skipped_count)
                    record = {
                        "record_type": "skipped_range",
                        "failure_class": "overdue",
                        "first_slot_id": first.slot_id,
                        "last_slot_id": last.slot_id,
                        "slot_id": first.slot_id,
                        "slot_at_utc": utc_text(first.scheduled_utc_ns),
                        "last_slot_at_utc": utc_text(last.scheduled_utc_ns),
                        "slot_count": skipped_count,
                        "clock_epoch": first.clock_epoch,
                        "status": "skipped",
                        "member": None,
                    }
                    self.metrics.result("overdue", skipped_count)
                    self._count("overdue", skipped_count)
                    await self._submit(self._persist_quietly, first, record)

        finally:
            self._running = False
            await self.flush()
            try:
                self._finish()
            except OSError:
                self._abandon()
            self.io.shutdown(wait=True)
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)


def collect(config: CaptureConfig) -> None:
    configure("collector")
    collector = Collector(config)
    server = collector.metrics.serve(config.metrics_port)
    try:
        asyncio.run(collector.run())
    finally:
        server.shutdown()
        server.server_close()
