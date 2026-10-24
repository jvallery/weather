# SPDX-License-Identifier: MIT
"""Bounded read-only aggregate callback over complete immutable evidence windows."""

import asyncio
import base64
import hashlib
import json
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from threading import Lock
from types import MappingProxyType

from pydantic import ValidationError

from weather.db.public_evidence import EvidenceWindow, SupportEvidence, read_evidence_window
from weather.db.public_source import PublicSqlSource
from weather.products.aggregate import Interval as EngineInterval

from .cursors import Snapshot, watermark
from .metric_registry import REGISTRY_REVISION, PublicInputError
from .public_common import utc_instant
from .public_models import Aggregate, AggregateQuery, Aggregates, Interval
from .public_service import SourcePage
from .support_assembly import (
    ASSEMBLY_REVISION,
    Assembly,
    SupportPolicy,
    assemble,
    compute_window,
    required_read_spans,
)

SOURCE_REVISION = "complete_aggregate_page_v1"
MAX_EVIDENCE_REFERENCES = 50000
_PROCESS_SLOT = Lock()
_MICROS = 1_000_000


def _input_error() -> PublicInputError:
    return PublicInputError("invalid_cursor", "Invalid aggregate source position.")


def _unavailable() -> PublicInputError:
    return PublicInputError("unavailable", "Aggregate source is temporarily unavailable.")


def _overflow() -> PublicInputError:
    return PublicInputError(
        "range_too_large",
        "Complete aggregate evidence exceeds a bounded page budget; "
        "select a smaller page or source interval.",
    )


def _micros(span: timedelta) -> int:
    return (span.days * 86400 + span.seconds) * _MICROS + span.microseconds


def encode_position(ordinal: int, evaluated_at: datetime) -> str:
    if type(ordinal) is not int or ordinal < 1:
        raise ValueError("Invalid aggregate ordinal")
    stamp = utc_instant(evaluated_at).isoformat(timespec="microseconds").replace("+00:00", "Z")
    body = json.dumps([1, ordinal, stamp], separators=(",", ":")).encode()
    return "a1_" + base64.urlsafe_b64encode(body).decode().rstrip("=")


def decode_position(value: str, total_items: int, now: datetime) -> tuple[int, datetime]:
    try:
        if len(value) > 512 or re.fullmatch(r"a1_[A-Za-z0-9_-]+", value) is None:
            raise ValueError("Position shape")
        encoded = value[3:]
        data = json.loads(
            base64.b64decode(
                encoded + "=" * (-len(encoded) % 4),
                altchars=b"-_",
                validate=True,
            )
        )
        if (
            type(data) is not list
            or len(data) != 3
            or type(data[0]) is not int
            or data[0] != 1
            or type(data[1]) is not int
            or not 1 <= data[1] < total_items
            or type(data[2]) is not str
        ):
            raise ValueError("Position fields")
        ordinal, evaluated_at = data[1], utc_instant(data[2])
        if evaluated_at > now or encode_position(ordinal, evaluated_at) != value:
            raise ValueError("Noncanonical/future position")
        return ordinal, evaluated_at
    except ValueError, TypeError, KeyError, UnicodeError, OverflowError:
        raise _input_error() from None


def _roles(metric: str, statistic: str) -> tuple[str, ...]:
    return (
        ("wind_avg", "wind_direction", "wind_gust")
        if (metric == "wind_direction" and statistic == "vector_mean")
        else (metric,)
    )


@dataclass
class _Budget:
    maximum: int
    spent: int = 0

    async def charge(self, amount: int = 1) -> None:
        previous = self.spent
        self.spent += amount
        if self.spent > self.maximum:
            raise _overflow()
        if previous // 128 != self.spent // 128:
            await asyncio.sleep(0)


@dataclass(frozen=True)
class _Stream:
    metric: str
    rows: tuple[SupportEvidence, ...]
    times: tuple[datetime, ...]


async def _index(evidence: EvidenceWindow, budget: _Budget) -> dict[str, tuple[_Stream, ...]]:
    grouped: dict[tuple[str, str, str], list[SupportEvidence]] = defaultdict(list)
    for row in evidence.rows:
        await budget.charge()
        grouped[(row.point.metric, row.device_alias, row.family)].append(row)
    by_metric: dict[str, list[_Stream]] = defaultdict(list)
    for (metric, _device, _family), rows in sorted(grouped.items()):
        await budget.charge(len(rows))
        ordered = tuple(
            sorted(rows, key=lambda row: (row.point.observed_at, row.point.evidence_id))
        )
        by_metric[metric].append(
            _Stream(metric, ordered, tuple(row.point.observed_at for row in ordered))
        )
    return {metric: tuple(streams) for metric, streams in by_metric.items()}


async def _inputs_for(
    window: EngineInterval,
    roles: tuple[str, ...],
    policies: Mapping[str, SupportPolicy],
    index: Mapping[str, tuple[_Stream, ...]],
    bound: str,
    budget: _Budget,
) -> EvidenceWindow:
    """Canonical per-bin/per-role reader-equivalent subset, independent of page size."""
    selected: list[SupportEvidence] = []
    for metric in roles:
        policy = policies.get(metric)
        spans = required_read_spans({metric: policy} if policy is not None else {})
        lower = window.start - timedelta(seconds=spans.preceding_hold_seconds)
        upper = window.end + timedelta(seconds=spans.following_report_seconds)
        for stream in index.get(metric, ()):
            await budget.charge()
            start = bisect_left(stream.times, window.start)
            end = bisect_right(stream.times, upper)
            await budget.charge(end - start)
            selected.extend(stream.rows[start:end])
            preceding = start - 1
            while preceding >= 0 and stream.times[preceding] >= lower:
                await budget.charge()
                if stream.rows[preceding].source_time_eligible:
                    stamp = stream.times[preceding]
                    first = bisect_left(stream.times, stamp)
                    await budget.charge(preceding - first + 1)
                    selected.extend(
                        row
                        for row in stream.rows[first : preceding + 1]
                        if row.source_time_eligible
                    )
                    break
                preceding -= 1
    return EvidenceWindow(bound, tuple(selected))


async def _preflight_compute(assembled: Assembly, statistic: str, budget: _Budget) -> None:
    """Bound the engine's active-set sweep cost before entering synchronous math.

    An input-count cap alone cannot bound cells(): staggered long overlapping
    holds can require scanning/sorting a large active set at every boundary.
    Wind/pressure invoke multiple sweeps, so charge conservative repeated passes.
    """
    repeats = 5 if statistic == "trend" else 3 if statistic == "vector_mean" else 1
    for supports in assembled.supports.values():
        size = len(supports)
        await budget.charge(repeats * size * max(1, size.bit_length()))
        events: dict[datetime, int] = defaultdict(int)
        for support in supports:
            await budget.charge()
            interval = support.interval.intersect(assembled.window) if support.interval else None
            if interval is not None:
                events[interval.start] += 1
                events[interval.end] -= 1
        active = 0
        previous = assembled.window.start
        for stamp, change in sorted(events.items()):
            await budget.charge()
            if stamp > previous:
                await budget.charge(repeats * active * max(1, active.bit_length()))
            active += change
            previous = stamp


class AggregateSource:
    """Callable PublicSqlSource aggregate reader; no pool, worker or task is created.

    One process-wide nonwaiting slot covers I/O and CPU. Cancellation is cooperative,
    never offloaded to a detached thread/task; all paths release the slot in finally.
    """

    def __init__(
        self,
        reader: PublicSqlSource,
        policies: Mapping[str, SupportPolicy],
        *,
        pressure_endpoint_seconds: int | None,
        pressure_minimum_fraction: Fraction | None,
        clock: Callable[[], datetime],
        maximum_inputs: int = 50000,
        maximum_work: int = 250000,
    ) -> None:
        if (
            type(maximum_inputs) is not int
            or not 1 <= maximum_inputs <= 50000
            or type(maximum_work) is not int
            or not 1 <= maximum_work <= 1_000_000
        ):
            raise ValueError("Invalid aggregate source budgets")
        if (pressure_endpoint_seconds is None) != (pressure_minimum_fraction is None) or (
            pressure_endpoint_seconds is not None
            and (
                type(pressure_endpoint_seconds) is not int
                or not 1 <= pressure_endpoint_seconds <= 86400
                or pressure_minimum_fraction is None
                or not 0 < pressure_minimum_fraction <= 1
            )
        ):
            raise ValueError("Invalid explicit pressure endpoint policy")
        self._reader, self._policies = reader, MappingProxyType(dict(policies))
        self._endpoint, self._minimum = pressure_endpoint_seconds, pressure_minimum_fraction
        self._clock, self._maximum_inputs, self._maximum_work = clock, maximum_inputs, maximum_work
        self._closed = False
        self._active: asyncio.Task[object] | None = None

    async def __call__(
        self, query: AggregateQuery, snapshot: Snapshot | None
    ) -> SourcePage[Aggregates]:
        if self._closed:
            raise _unavailable()
        try:
            query = AggregateQuery.model_validate(query.model_dump())
        except PublicInputError:
            raise
        except Exception:
            raise PublicInputError("invalid_request", "Invalid aggregate selection.") from None
        if query.cursor is not None:
            raise _input_error()
        if not _PROCESS_SLOT.acquire(blocking=False):
            raise _unavailable()
        self._active = asyncio.current_task()
        try:
            return await self._page(query, snapshot)
        except PublicInputError:
            raise
        except ValidationError as error:
            if any(issue["type"] == "too_long" for issue in error.errors(include_input=False)):
                raise _overflow() from None
            raise _unavailable() from None
        except Exception:
            # Source/mapper/validation exceptions can contain private driver values.
            raise _unavailable() from None
        finally:
            self._active = None
            _PROCESS_SLOT.release()

    async def _page(
        self, query: AggregateQuery, snapshot: Snapshot | None
    ) -> SourcePage[Aggregates]:
        now = utc_instant(self._clock())
        metrics = tuple(sorted(query.metrics))
        width = query.resolution_seconds * _MICROS
        duration = _micros(query.end - query.start)
        bins = (duration + width - 1) // width
        total = bins * len(metrics)
        ordinal, evaluated_at = (
            (0, now)
            if snapshot is None
            else decode_position(
                snapshot.position,
                total,
                now,
            )
        )
        stop = min(total, ordinal + query.limit)

        def bin_window(index: int) -> EngineInterval:
            return EngineInterval(
                query.start + timedelta(microseconds=index * width),
                query.start + timedelta(microseconds=min(duration, (index + 1) * width)),
            )

        first, last = bin_window(ordinal // len(metrics)), bin_window((stop - 1) // len(metrics))
        roles = tuple(
            sorted({role for metric in metrics for role in _roles(metric, query.statistic)})
        )
        policies = {metric: self._policies[metric] for metric in roles if metric in self._policies}
        spans = required_read_spans(policies)
        evidence = await read_evidence_window(
            self._reader,
            Interval(start=first.start, end=last.end),
            roles,
            preceding_hold_seconds=spans.preceding_hold_seconds,
            following_report_seconds=spans.following_report_seconds,
            snapshot_watermark=snapshot.watermark if snapshot else None,
            maximum_points=self._maximum_inputs,
            evaluated_at=evaluated_at,
            enforce_raw_range=False,
        )
        bound = watermark(evidence.watermark)
        if snapshot is not None and bound != snapshot.watermark:
            raise _unavailable()
        if len(evidence.rows) > self._maximum_inputs:
            raise _overflow()
        budget = _Budget(self._maximum_work)
        index = await _index(evidence, budget)
        items: list[Aggregate] = []
        evidence_references = 0
        for position in range(ordinal, stop):
            await asyncio.sleep(0)
            metric = metrics[position % len(metrics)]
            interval = bin_window(position // len(metrics))
            selected_roles = _roles(metric, query.statistic)
            selected_policies = {
                name: policies[name] for name in selected_roles if name in policies
            }
            selected = await _inputs_for(
                interval, selected_roles, selected_policies, index, bound, budget
            )
            await budget.charge(len(selected.rows))
            assembled = assemble(interval, selected, selected_policies)
            if (
                query.statistic == "trend"
                and self._endpoint is not None
                and (interval.seconds < 2 * self._endpoint)
            ):
                raise PublicInputError(
                    "invalid_request",
                    "Aggregate bin is shorter than the commissioned pressure endpoint windows.",
                    suggested_resolution_seconds=2 * self._endpoint,
                )
            await _preflight_compute(assembled, query.statistic, budget)
            result = compute_window(
                assembled,
                metric,
                query.statistic,
                pressure_endpoint_seconds=self._endpoint,
                pressure_minimum_fraction=self._minimum,
            )
            evidence_references += len(result.aggregate.evidence_ids)
            if evidence_references > MAX_EVIDENCE_REFERENCES:
                raise _overflow()
            items.append(result.aggregate)
        revision = (
            "aggregate_page_v1_"
            + hashlib.sha256(
                json.dumps(
                    {
                        "recipe": SOURCE_REVISION,
                        "assembly": ASSEMBLY_REVISION,
                        "registry": REGISTRY_REVISION,
                        "start": query.start.isoformat(),
                        "end": query.end.isoformat(),
                        "metrics": metrics,
                        "resolution_seconds": query.resolution_seconds,
                        "statistic": query.statistic,
                        "watermark": bound,
                        "evaluated_at": evaluated_at.isoformat(),
                        "policies": {
                            name: policy.binding() for name, policy in sorted(policies.items())
                        },
                        "pressure_endpoint_seconds": self._endpoint,
                        "pressure_minimum_fraction": None
                        if self._minimum is None
                        else str(self._minimum),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
        )
        if self._closed:
            raise _unavailable()
        return SourcePage(
            Aggregates(
                revision=revision,
                generated_at=utc_instant(self._clock()),
                interval=Interval(start=query.start, end=query.end),
                resolution_seconds=query.resolution_seconds,
                statistic=query.statistic,
                items=tuple(items),
                next_cursor=None,
            ),
            bound,
            encode_position(stop, evaluated_at) if stop < total else None,
        )

    async def aclose(self, *, timeout_seconds: float = 1) -> None:
        if not 0 < timeout_seconds <= 5:
            raise ValueError("Invalid aggregate shutdown bound")
        self._closed = True
        task = self._active
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=timeout_seconds)
