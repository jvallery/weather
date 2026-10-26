# SPDX-License-Identifier: MIT
"""Monotonic scheduling, wall-clock discontinuities and bounded source estimates."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime


def utc_text(ns: int, *, upper: bool = False) -> str:
    seconds, nanos = divmod(ns, 1_000_000_000)
    micros = (nanos + 999) // 1000 if upper else nanos // 1000
    if micros == 1_000_000:
        seconds, micros = seconds + 1, 0
    return (
        datetime.fromtimestamp(seconds, UTC)
        .replace(microsecond=micros)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class Slot:
    slot_id: str
    scheduled_mono_ns: int
    scheduled_utc_ns: int
    clock_epoch: str


class Schedule:
    def __init__(self, wall_ns: int, mono_ns: int, cadence_ms: int, phase_ms: int) -> None:
        self.period_ns = cadence_ms * 1_000_000
        self.phase_ns = phase_ms * 1_000_000
        delta = (self.phase_ns - wall_ns) % self.period_ns
        self.next_mono_ns = mono_ns + delta
        self.offset_ns = wall_ns - mono_ns
        self.epoch = uuid.uuid4().hex
        self.seq = 0

    def grid(self, wall_ns: int) -> int:
        """Canonical (camera, slot) key: the nearest cadence grid point, identical per writer."""
        steps = (wall_ns - self.phase_ns + self.period_ns // 2) // self.period_ns
        return steps * self.period_ns + self.phase_ns

    def observe_clock(self, wall_ns: int, mono_ns: int) -> bool:
        offset = wall_ns - mono_ns
        expected = self.grid(self.next_mono_ns + self.offset_ns)
        # Re-anchor the monotonic deadline to the wall grid; this also restores phase after steps.
        wall = self.next_mono_ns + offset
        key = self.grid(wall)
        # A re-anchor that repeats or skips a key is a discontinuity, however small the step.
        changed = abs(offset - self.offset_ns) > 1_000_000_000 or key != expected
        if changed:
            self.epoch = uuid.uuid4().hex
        self.offset_ns = offset
        self.next_mono_ns += key - wall
        return changed

    def _slot(self, seq: int, mono_ns: int) -> Slot:
        # Epoch+sequence cannot reuse an identifier after backwards clock steps or restart.
        return Slot(f"{self.epoch}-{seq}", mono_ns, self.grid(mono_ns + self.offset_ns), self.epoch)

    def take(self) -> Slot:
        slot = self._slot(self.seq, self.next_mono_ns)
        self.seq += 1
        self.next_mono_ns += self.period_ns
        return slot

    def skip(self, count: int) -> tuple[Slot, Slot]:
        if count < 1:
            raise ValueError("Skip count must be positive")
        first = self.take()
        self.seq += count - 1
        self.next_mono_ns += (count - 1) * self.period_ns
        return first, self._slot(self.seq - 1, self.next_mono_ns - self.period_ns)

    def overdue_count(self, now_ns: int) -> int:
        """Slots more than three quarters of a period late are skipped (#145: 1.5 s at 0.5 Hz);
        a later slot is still taken. A late capture keeps its slot key and records its true
        request time, which the observed-exposure renderer uses."""
        late = now_ns - self.next_mono_ns - self.period_ns * 3 // 4
        return max(0, (late + self.period_ns - 1) // self.period_ns)


def request_timing(
    started_wall_ns: int,
    ended_wall_ns: int,
    started_mono_ns: int,
    ended_mono_ns: int,
    cache_allowance_ms: int | None,
    calibration_revision: str | None,
) -> dict[str, object]:
    discontinuity = (
        abs((ended_wall_ns - started_wall_ns) - (ended_mono_ns - started_mono_ns)) > 1_000_000_000
    )
    # The frame cannot postdate the response, so the upper bound needs only a continuous clock.
    # A mounted cache calibration refines the lower bound and freshness, never the upper bound.
    bounded = cache_allowance_ms is not None and not discontinuity
    lower = started_wall_ns - (cache_allowance_ms or 0) * 1_000_000
    midpoint = ((started_wall_ns + ended_wall_ns) // 2) // 1000 * 1000
    upper = (ended_wall_ns + 999) // 1000 * 1000
    lower = lower // 1000 * 1000
    return {
        "request_started_at_utc": utc_text(started_wall_ns),
        "response_completed_at_utc": utc_text(ended_wall_ns, upper=True),
        "monotonic_started_ns": str(started_mono_ns),
        "monotonic_completed_ns": str(ended_mono_ns),
        "source_at_utc": utc_text(midpoint),
        "source_time_basis": "calibrated_request_midpoint" if bounded else "request_midpoint",
        "source_lower_bound": utc_text(lower) if bounded else None,
        "capture_upper_bound": None if discontinuity else utc_text(ended_wall_ns, upper=True),
        "uncertainty_ms": max(midpoint - lower, upper - midpoint) / 1_000_000 if bounded else None,
        "freshness": "plausible" if bounded else "unknown",
        "calibration_revision": calibration_revision if bounded else None,
        "clock_discontinuity": discontinuity,
    }


class Backoff:
    def __init__(self) -> None:
        self.failures = 0

    def success(self) -> None:
        self.failures = 0

    def failure(self, authentication: bool = False) -> int:
        if authentication:
            return 60
        delay = (1, 2, 4, 8, 16, 30)[min(self.failures, 5)]
        self.failures += 1
        return delay
