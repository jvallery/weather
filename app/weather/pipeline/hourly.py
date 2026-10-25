# SPDX-License-Identifier: MIT
"""The pipeline's hourly pass (#38): one private native original per camera and UTC hour.

Each loop, every hour slot that ended within the last 12 h, whose chunks have had time to seal
and that has no active selection, is selected from the camera's sealed captures
(`weather.stills.policy`). The choice is re-derived from the sealed chunks, not from in-memory
renderer state, so a restart selects the same capture. A winner's exact TAR member bytes are
archived to private `weather-originals` and its sanitized derivatives to `weather-media`
through #18's publish, with the selection row (bound to its slot, calendar day, #15 segment and
solar cycle) committed in the same transaction. A missing slot is recorded with its reason and
no upload. A crash anywhere before that commit leaves no selection, and the rerun converges on
one: objects are content-addressed and never overwritten, and an older intent is fenced.
"""

import asyncio
import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from weather.publish import catalog as publish_catalog
from weather.publish.model import EMBARGO, Committed, Product, ProductFile
from weather.publish.publisher import Publisher
from weather.stills import catalog
from weather.stills.policy import HOUR, Choice, choose
from weather.stills.sanitize import derive
from weather.timemodel import HourSlot, slot_for
from weather.timemodel._instant import epoch_seconds

from . import metrics
from .ffmpeg import decode_jpeg
from .intake import Candidate, ChunkIndex, Expected, IntakeError, read_frame, utc
from .render import SEAL_MARGIN

LOG = logging.getLogger("weather.pipeline")
LOOKBACK = timedelta(hours=12)
PUBLISH_DEADLINE = timedelta(hours=1)
MAX_BACKOFF = 1800
LOOP_SECONDS = 10
MICROSECOND = Fraction(1, 1_000_000)


class Unbound(RuntimeError):
    """No current #15 segment contains the winner yet (schedule not frozen); retried."""


@dataclass
class HourlyArchive:
    camera: str
    engine: AsyncEngine
    publisher: Publisher
    index: ChunkIndex
    expected: Expected
    chunk_seconds: int = 60

    def __post_init__(self) -> None:
        self.backoff: dict[datetime, tuple[int, datetime]] = {}

    @property
    def settle(self) -> timedelta:
        """After the hour, every chunk holding one of its captures has been sealed."""
        seconds = self.expected.max_lag + self.chunk_seconds + 2 * SEAL_MARGIN
        return timedelta(seconds=float(seconds))

    def due(self, now: datetime) -> list[datetime]:
        last = slot_for(now - self.settle).start - timedelta(hours=1)
        first = slot_for(now - LOOKBACK).start
        slots = []
        while last >= first:
            slots.append(last)
            last -= timedelta(hours=1)
        return sorted(slots)

    def _decode(self, candidate: Candidate) -> tuple[bytes, bytes] | str:
        try:
            data = read_frame(candidate)  # the sealed member, re-verified against its sha256
        except IntakeError:
            return "changed"
        rgb = decode_jpeg(data, self.expected.width, self.expected.height)
        return (data, rgb) if rgb is not None else "decode"

    def select(self, slot: datetime) -> Choice:
        start = int(epoch_seconds(slot))
        self.index.refresh(Fraction(start), Fraction(start + HOUR) - MICROSECOND)
        if self.index.unreadable:
            # A chunk that may hold the winner failed to read: never decide without it.
            raise OSError("Sealed chunk unreadable; selection deferred")
        candidates = [c for chunk in self.index.chunks.values() for c in chunk.candidates]
        return choose(candidates, start, self._decode)

    async def step(self, now: datetime) -> None:
        slots = self.due(now)
        if not slots:
            return
        async with self.engine.connect() as connection:
            done = await catalog.selected(connection, self.camera, slots[0], slots[-1])
        pending = [slot for slot in slots if slot not in done]
        self.backoff = {k: v for k, v in self.backoff.items() if k in pending}
        metrics.STILL_PENDING.labels(self.camera).set(len(pending))
        metrics.STILL_OLDEST.labels(self.camera).set(
            (now - pending[0] - timedelta(hours=1)).total_seconds() if pending else 0
        )
        for slot in pending:
            if slot in self.backoff and now < self.backoff[slot][1]:
                continue
            stage = "select"
            try:
                choice = await asyncio.to_thread(self.select, slot)
                stage = "archive"
                await self.archive(slot_for(slot), choice, now)
                metrics.STILL_SLOTS.labels(self.camera, choice.state).inc()
                self.backoff.pop(slot, None)
            except Exception as error:
                stage = "bind" if isinstance(error, Unbound) else stage
                metrics.STILL_FAILURES.labels(self.camera, stage).inc()
                LOG.error("Hourly %s failed (%s, %s)", stage, self.camera, type(error).__name__)
                failures = self.backoff.get(slot, (0, now))[0] + 1
                delay = min(MAX_BACKOFF, LOOP_SECONDS * 2 ** min(failures - 1, 10))
                self.backoff[slot] = (failures, now + timedelta(seconds=delay))

    async def archive(self, slot: HourSlot, choice: Choice, now: datetime) -> None:
        winner = choice.winner
        if winner is None:
            async with self.engine.begin() as connection:
                await catalog.record(connection, camera=self.camera, slot=slot, choice=choice)
            return
        assert winner.upper is not None and choice.jpeg is not None and choice.rgb is not None
        source = utc(winner.time)
        product_identity = Product.still(
            camera_id=self.camera,
            slot_start=slot.start,
            source_start=slot.start,
            source_end=slot.end,
            public_not_before=slot.end + EMBARGO,
            files=(),
        )
        async with self.engine.connect() as connection:
            segment = await catalog.segment_for(connection, self.camera, source)
            current = await publish_catalog.current(connection, product_identity)
        if segment is None:
            raise Unbound("No segment contains the winner")
        original = catalog.Original(
            sha256=winner.sha256,
            bytes=winner.size,
            width=self.expected.width,
            height=self.expected.height,
        )
        # A source interval is never empty; the embargo runs from the capture upper bound.
        end = utc(max(winner.upper, winner.time + MICROSECOND))
        with tempfile.TemporaryDirectory(prefix="weather-still-") as directory:
            root = Path(directory)
            # The exact sealed member bytes: no re-encode, no metadata change.
            (root / "original.jpg").write_bytes(choice.jpeg)
            files = [
                ProductFile(
                    role="original",
                    extension="jpg",
                    path=root / "original.jpg",
                    width=original.width,
                    height=original.height,
                )
            ]
            for item in derive(choice.rgb, self.expected.width, self.expected.height):
                path = root / f"{item.role}.{item.extension}"
                path.write_bytes(item.data)
                files.append(
                    ProductFile(
                        role=item.role,
                        extension=item.extension,
                        path=path,
                        width=item.width,
                        height=item.height,
                    )
                )
            product = Product.still(
                camera_id=self.camera,
                slot_start=slot.start,
                source_start=source,
                source_end=end,
                public_not_before=end + EMBARGO,
                files=tuple(files),
            )

            async def then(connection: AsyncConnection, committed: Committed) -> None:
                await catalog.record(
                    connection,
                    camera=self.camera,
                    slot=slot,
                    choice=choice,
                    segment=segment,
                    original=original,
                    committed=committed,
                )

            try:
                await self.publisher.publish(
                    product,
                    expected_revision=current.revision,
                    deadline=now + PUBLISH_DEADLINE,
                    then=then,
                )
            except publish_catalog.Withdrawn:
                # A withdrawn still is never republished; its slot says so.
                LOG.warning("Hourly still was withdrawn; not republished (%s)", self.camera)
                withdrawn = Choice("missing", "withdrawn", None, None, choice.eligible)
                async with self.engine.begin() as connection:
                    await catalog.record(
                        connection, camera=self.camera, slot=slot, choice=withdrawn
                    )
                return
        metrics.STILL_ARCHIVED.labels(self.camera).inc()
