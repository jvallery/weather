# SPDX-License-Identifier: MIT
"""Transport diagnostics only; these intervals never qualify meteorological support."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

GapReason = Literal[
    "arrival_gap",
    "capacity_loss",
    "parse_failure",
    "reduced_cadence",
    "db_backlog",
    "station_unavailable",
    "restart_volatile_unknown",
    "spool_unreadable",
]
DeviceState = Literal["normal", "reduced", "unavailable", "unknown"]
NS = 1_000_000_000
MINUTE_NS = 60 * NS


@dataclass(frozen=True)
class Cadence:
    seconds: Decimal | None
    state: DeviceState
    policy: str = "reported-interval-transport-1"

    def __post_init__(self) -> None:
        if self.seconds is not None and (
            not self.seconds.is_finite() or self.seconds <= 0 or self.seconds > 86400
        ):
            raise ValueError("Invalid reported transport cadence")


@dataclass(frozen=True)
class DiagnosticInterval:
    start_ns: int
    end_ns: int
    reason: GapReason
    policy: str
    unknown_count: bool = False

    def __post_init__(self) -> None:
        if self.end_ns < self.start_ns:
            raise ValueError("Invalid diagnostic interval")

    def minutes(self) -> list[tuple[int, int, int]]:
        """UTC minute plus clipped [start,end) bounds, capped to one day per record."""
        if self.end_ns - self.start_ns > 86400 * NS:
            raise ValueError("Diagnostic interval requires bounded daily segments")
        result = []
        minute = self.start_ns // MINUTE_NS * MINUTE_NS
        while minute < self.end_ns:
            result.append(
                (minute, max(minute, self.start_ns), min(minute + MINUTE_NS, self.end_ns))
            )
            minute += MINUTE_NS
        return result


def arrival_interval(
    previous_receive_ns: int | None,
    received_ns: int,
    previous: Cadence | None,
    current: Cadence,
) -> DiagnosticInterval | None:
    """Use original receive time on replay; late/out-of-order rows create no new gap.

    A new slower report interval may describe the preceding interval. At transitions
    use the slower of two known cadences rather than call intentional reduction loss.
    Unknown cadence cannot establish a missing expected packet.
    """
    if previous_receive_ns is None or received_ns <= previous_receive_ns:
        return None
    if current.state == "unavailable":
        return DiagnosticInterval(
            previous_receive_ns, received_ns, "station_unavailable", current.policy
        )
    candidates = [c.seconds for c in (previous, current) if c is not None and c.seconds is not None]
    if not candidates or current.seconds is None:
        return None
    cadence = max(candidates)
    if current.state == "reduced":
        return DiagnosticInterval(
            previous_receive_ns, received_ns, "reduced_cadence", current.policy
        )
    start = previous_receive_ns + int(cadence * NS)
    if received_ns <= start:
        return None
    return DiagnosticInterval(start, received_ns, "arrival_gap", current.policy)


def source_cadence(report_minutes: Decimal | None, state: DeviceState = "unknown") -> Cadence:
    """No guessed rapid-wind/status cadence and no rain support from this value."""
    return Cadence(None if report_minutes is None else report_minutes * 60, state)
