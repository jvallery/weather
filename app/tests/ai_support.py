# SPDX-License-Identifier: MIT
"""Fakes for #23's tests: recorded Cortex replies, a slot that counts calls in flight, static
aggregates and media. Nothing here contacts Cortex, Garage or FFmpeg."""

import io
import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
from PIL import Image as PILImage
from weather.ai.client import Cortex, Image
from weather.ai.media import Source, WindowMedia
from weather.ai.slot import Uncertain
from weather.ai.windows import Window, samples
from weather.api.models import Aggregate, Coverage

FIXTURE = json.loads((Path(__file__).parent / "fixtures/ai-recorded.json").read_text())


def context_of(body: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = json.loads(body["messages"][1]["content"][0]["text"])
    return context


def _fill(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, list):
        out: list[Any] = []
        for item in value:
            if item == "$EVIDENCE":
                out.extend(e["id"] for e in context["evidence"])
            else:
                out.append(_fill(item, context))
        return out
    if isinstance(value, dict):
        return {k: _fill(v, context) for k, v in value.items()}
    if isinstance(value, str) and value.startswith("$"):
        if value == "$FRAME0":
            return next(e["id"] for e in context["evidence"] if e["kind"] == "frame")
        if value.startswith("$V:"):
            category = value[3:]
            return next(
                o["id"] for o in context["visual_observations"] if o["category"] == category
            )
        if value.startswith("$M:"):
            _, metric, statistic = value.split(":")
            return next(
                m["id"]
                for m in context["instrument_metrics"]
                if m["metric"] == metric and m["statistic"] == statistic
            )
    return value


def recorded(stage: str, name: str, body: dict[str, Any]) -> dict[str, Any]:
    """The chat-completion JSON for a fixture case, with IDs bound to this request."""
    case = FIXTURE[stage][name]
    content = case["content"]
    if not isinstance(content, str):
        content = json.dumps(_fill(content, context_of(body)), ensure_ascii=False)
    return {
        "id": "chatcmpl-synthetic",
        "object": "chat.completion",
        "model": FIXTURE["model"],
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": case["finish_reason"],
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


Step = tuple[Any, ...]


class Script:
    """A Cortex transport answering each call from a list of steps (the last one repeats).

    Steps: ("visual"|"narrative", case) a recorded reply; ("status", code, headers);
    ("down",) a connection failure; ("timeout",) a client timeout."""

    def __init__(self, steps: Sequence[Step] | Callable[[dict[str, Any]], Step]) -> None:
        self.steps = steps
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if callable(self.steps):
            step = self.steps(body)
        else:
            step = self.steps[min(len(self.bodies), len(self.steps)) - 1]
        if step[0] == "down":
            raise httpx2.ConnectError("synthetic", request=request)
        if step[0] == "timeout":
            raise httpx2.ReadTimeout("synthetic", request=request)
        if step[0] == "status":
            return httpx2.Response(step[1], headers=step[2] if len(step) > 2 else {}, json={})
        return httpx2.Response(200, json=recorded(step[0], step[1], body))

    def cortex(self) -> Cortex:
        return Cortex("synthetic-key", transport=httpx2.MockTransport(self))


def by_task(visual: str = "valid", narrative: str = "valid") -> Callable[[dict[str, Any]], Step]:
    def choose(body: dict[str, Any]) -> Step:
        task = context_of(body)["task"]
        return ("visual", visual) if task == "visual_analysis" else ("narrative", narrative)

    return choose


class CountingSlot:
    """In-process stand-in for the advisory slot that records the peak calls in flight."""

    def __init__(self) -> None:
        self.active = self.peak = 0

    @asynccontextmanager
    async def hold(self, wait: float) -> AsyncIterator[Uncertain]:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            yield Uncertain()
        finally:
            self.active -= 1


class RawSink:
    """An engine whose transactions record raw-response inserts and grant call-budget spends
    until `budget` is used up."""

    def __init__(self, budget: int | None = None) -> None:
        self.rows: list[dict[str, Any]] = []
        self.spent = 0
        self.budget = budget

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[RawSink]:
        yield self

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        if "attempt_calls" in str(statement):
            granted = self.budget is None or self.spent < self.budget
            self.spent += granted
            return SimpleNamespace(rowcount=int(granted))
        self.rows.append(dict(params or {}))
        return SimpleNamespace(rowcount=1)


def aggregate(
    metric: str,
    statistic: str,
    value: float | None,
    unit: str,
    *,
    coverage: float = 1.0,
    count: int = 60,
    digest: str = "e" * 64,
    start: datetime,
    end: datetime,
) -> Aggregate:
    expected = (end - start).total_seconds()
    return Aggregate(
        metric=metric,
        statistic=statistic,
        start=start,
        end=end,
        value=value,
        unit=unit,
        coverage=Coverage(
            fraction=coverage, support_seconds=coverage * expected, expected_seconds=expected
        ),
        evidence_count=count,
        evidence_hash=digest,
        reason=None,
    )


def station(start: datetime, end: datetime, **overrides: Any) -> list[Aggregate]:
    """A day of measurements: 3.2–14.8 °C, gust 12.4 m/s, mean wind 5.1 m/s, no rain."""
    values = {
        ("temperature", "min"): (3.2, "degC"),
        ("temperature", "max"): (14.8, "degC"),
        ("temperature", "mean"): (9.1, "degC"),
        ("humidity", "mean"): (54.0, "%"),
        ("wind_avg", "mean"): (5.1, "m/s"),
        ("wind_gust", "max"): (12.4, "m/s"),
        ("precipitation", "sum"): (0.0, "mm"),
        ("station_pressure", "mean"): (842.3, "hPa"),
        ("lightning_count", "sum"): (0.0, "1"),
    }
    for key, value in overrides.items():
        metric, statistic = key.rsplit("_", 1)
        values[(metric, statistic)] = (value, values[(metric, statistic)][1])
    return [
        aggregate(metric, statistic, value, unit, start=start, end=end)
        for (metric, statistic), (value, unit) in values.items()
    ]


class StaticWeather:
    def __init__(self, aggregates: list[Aggregate]) -> None:
        self.items = aggregates
        self.reads = 0

    async def aggregates(
        self, start: datetime, end: datetime, metrics: tuple[tuple[str, tuple[str, ...]], ...]
    ) -> list[Aggregate]:
        self.reads += 1
        return list(self.items)


def tiny_jpeg(shade: int = 128) -> Image:
    buffer = io.BytesIO()
    PILImage.new("RGB", (32, 18), (shade, shade, 200)).save(buffer, "JPEG")
    return Image("image/jpeg", buffer.getvalue())


class StaticMedia:
    """Two-frame windows and an optional keogram, without Garage or FFmpeg."""

    def __init__(self, keogram: bool = True) -> None:
        self.keogram = keogram
        self.sources: list[Source] = []

    async def prepare(self, source: Source, windows: Sequence[Window]) -> list[WindowMedia]:
        self.sources.append(source)
        return [
            WindowMedia(
                window,
                tuple((index, tiny_jpeg()) for index in samples(window, 2)),
                tiny_jpeg(40) if self.keogram and source.keogram is not None else None,
            )
            for window in windows
            if window.available
        ]
