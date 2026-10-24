# SPDX-License-Identifier: MIT
"""Deterministic measured-event rules over canonical telemetry rows (#41).

Pure functions: no database, clock or inference. Each rule family carries a revision that pins
its policy; changing a policy means a new revision string and tests, never a silent edit.

- Precipitation onset: one event per qualified canonical ``evt_precip`` row. No threshold, merge
  window or hysteresis; duplicate receipts never reach here (the family's unique key).
- Daily gust maximum: once a local weather day has closed, the maximum valid ``wind_gust`` > 0
  among ``obs_st`` reports ending in (day start, day end]; ties go to the earlier source time,
  then the lower observation id. One key per station and day, never keyed by the timestamp.
- Lightning: per canonical ``evt_strike`` (event authority) or per ``obs_st`` report interval with
  a positive integer count (interval authority). Exactly one authority is active.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from weather.timemodel import WeatherDay, day_for

Authority = Literal["event", "interval"]
Origin = Literal["measured", "visual", "synthesis", "manual"]
Basis = Literal["instrument", "visual", "inference"]
Precision = Literal["second", "interval", "clip"]

# The selected counting authority for lightning; the other family is withdrawn if present.
LIGHTNING_AUTHORITY: Authority = "event"
REVISIONS = {
    "precipitation_onset": "precipitation_onset/1",
    "daily_gust_max": "daily_gust_max/1",
    "lightning_strike": "lightning_strike/1",
    "lightning_interval": "lightning_interval/1",
}
# Tempest v171 integer-second source times; one obs_st report covers the minute ending at it.
SECOND = timedelta(seconds=1)
REPORT = timedelta(seconds=60)
BASES: dict[str, Basis] = {"measured": "instrument", "visual": "visual", "synthesis": "inference"}


@dataclass(frozen=True)
class Observation:
    """One canonical family row, read through the worker's evidence columns only."""

    id: UUID
    station: UUID
    source_ts: datetime
    values: Mapping[str, Any]
    quality: Mapping[str, Any]

    @property
    def valid(self) -> bool:
        return self.quality.get("valid") is True and isinstance(self.quality.get("invalid"), list)

    def number(self, name: str) -> float | None:
        value = self.values.get(name)
        if (
            not self.valid
            or name in self.quality["invalid"]
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
        ):
            return None
        return float(value)

    @property
    def timing_known(self) -> bool:
        # A defensible one-second interval only for a valid, whole-second source time.
        return self.valid and self.source_ts.microsecond == 0


@dataclass(frozen=True)
class Candidate:
    """The content of one event revision as a rule or a supplied producer derived it."""

    key: str
    family: str
    origin: Origin
    start: datetime
    end: datetime
    precision: Precision
    rule_revision: str
    evidence: tuple[UUID, ...]
    measurement: Mapping[str, Any] = field(default_factory=dict)
    camera: str | None = None
    label: str | None = None
    basis: Basis | None = None

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None or not self.start < self.end:
            raise ValueError("An event needs a non-empty aware half-open interval")
        if self.origin != "manual" and (
            not self.evidence or len(set(self.evidence)) != len(self.evidence)
        ):
            raise ValueError("Automatic events need unique nonempty evidence")
        if len(self.evidence) > 256:
            raise ValueError("Too much evidence")
        if self.origin in ("visual", "synthesis") and self.precision != "clip":
            raise ValueError("Visual and synthesis events keep their clip interval")
        if self.origin != "manual" and self.basis not in (None, BASES[self.origin]):
            raise ValueError("Origin and basis disagree")

    @property
    def evidence_basis(self) -> Basis:
        if self.basis is not None:
            return self.basis
        if self.origin == "manual":
            raise ValueError("Manual events state their basis")
        return BASES[self.origin]

    @property
    def evidence_hash(self) -> str:
        """Identifies the automatic content; equal hashes mean nothing material changed."""
        content = {
            "family": self.family,
            "origin": self.origin,
            "basis": self.evidence_basis,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "precision": self.precision,
            "rule": self.rule_revision,
            "evidence": [str(item) for item in self.evidence],
            "measurement": self.measurement,
            "camera": self.camera,
            "label": self.label,
        }
        encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode()).hexdigest()

    def local_dates(self) -> list[date]:
        return local_dates(self.start, self.end)


def local_dates(start: datetime, end: datetime) -> list[date]:
    """Every America/Denver day the half-open interval intersects (23/24/25-hour days)."""
    first = day_for(start).local_date
    last = day_for(end - timedelta(microseconds=1)).local_date
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def precipitation_onset(row: Observation) -> Candidate | None:
    if not row.timing_known:
        # Unknown timing stays private evidence in telemetry; nothing public is invented.
        return None
    return Candidate(
        key=f"precipitation_onset:{row.id}",
        family="precipitation_onset",
        origin="measured",
        start=row.source_ts,
        end=row.source_ts + SECOND,
        precision="second",
        rule_revision=REVISIONS["precipitation_onset"],
        evidence=(row.id,),
    )


def lightning_strike(row: Observation, authority: Authority) -> Candidate | None:
    if authority != "event" or not row.timing_known:
        return None
    distance = row.number("distance")
    return Candidate(
        key=f"lightning_strike:{row.id}",
        family="lightning_strike",
        origin="measured",
        start=row.source_ts,
        end=row.source_ts + SECOND,
        precision="second",
        rule_revision=REVISIONS["lightning_strike"],
        evidence=(row.id,),
        measurement={"strikes": 1, "distance_km": distance},
    )


def lightning_interval(row: Observation, authority: Authority) -> Candidate | None:
    count = row.number("lightning_strike_count")
    if authority != "interval" or not row.timing_known or count is None:
        return None
    if count < 1 or not count.is_integer():
        return None
    # The count covers the report interval ending at the source time (v171: report_interval
    # minutes); a missing or invalid interval falls back to the one-minute cadence.
    minutes = row.number("report_interval")
    support = timedelta(minutes=minutes) if minutes and minutes > 0 else REPORT
    return Candidate(
        key=f"lightning_interval:{row.id}",
        family="lightning_interval",
        origin="measured",
        start=row.source_ts - support,
        end=row.source_ts,
        precision="interval",
        rule_revision=REVISIONS["lightning_interval"],
        evidence=(row.id,),
        measurement={
            "strikes": int(count),
            "distance_km": row.number("lightning_strike_avg_distance"),
        },
    )


def gust_key(station: UUID, local_date: date) -> str:
    return f"daily_gust_max:{station}:{local_date.isoformat()}"


def daily_gust(day: WeatherDay, station: UUID, rows: Iterable[Observation]) -> Candidate | None:
    """The day's observed maximum gust with its metric coverage; None when no gust > 0."""
    home = [row for row in rows if row.station == station and day.start < row.source_ts <= day.end]
    gusts = [(row, gust) for row in home if (gust := row.number("wind_gust")) is not None]
    # Coverage: the union of each valid report's support minute, clipped to the day.
    spans = sorted(
        (max(row.source_ts - REPORT, day.start), min(row.source_ts, day.end)) for row, _ in gusts
    )
    support = timedelta()
    reach = day.start
    for lo, hi in spans:
        lo = max(lo, reach)
        if hi > lo:
            support += hi - lo
            reach = hi
    positive = [(row, gust) for row, gust in gusts if gust > 0 and row.timing_known]
    if not positive:
        return None
    winner, gust = min(positive, key=lambda item: (-item[1], item[0].source_ts, item[0].id))
    expected = (day.end - day.start).total_seconds()
    return Candidate(
        key=gust_key(station, day.local_date),
        family="daily_gust_max",
        origin="measured",
        start=winner.source_ts - REPORT,
        end=winner.source_ts,
        precision="interval",
        rule_revision=REVISIONS["daily_gust_max"],
        evidence=(winner.id,),
        measurement={
            "gust_mps": gust,
            "day": day.local_date.isoformat(),
            "coverage": {
                "fraction": round(support.total_seconds() / expected, 6),
                "support_seconds": support.total_seconds(),
                "expected_seconds": expected,
            },
        },
    )


def derive(family: str, row: Observation, authority: Authority) -> Candidate | None:
    """The row-keyed rules; a row of a family the authority excludes yields nothing."""
    if family == "precipitation_onset":
        return precipitation_onset(row)
    if family == "lightning_strike":
        return lightning_strike(row, authority)
    if family == "lightning_interval":
        return lightning_interval(row, authority)
    raise ValueError("Not a row-keyed rule family")
