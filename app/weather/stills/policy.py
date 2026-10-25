# SPDX-License-Identifier: MIT
"""Hourly selection policy (Q03): one native original per camera and UTC hour slot.

Among the hour's eligible captures (known timing, native geometry and commissioned settings,
then a clean JPEG decode whose bytes still match the sealed manifest), the one whose source
time is nearest `slot start + 30 min` wins; a tie goes to the earlier source time, then the
lower capture (attempt) ID. Within 60 s of the anchor is `on_target`; otherwise the hour's
nearest is `off_target` with its exact offset. With no eligible capture the slot is `missing`
with a reason. Only the source time decides membership: a capture never fills another hour,
and its file name, chunk and arrival order never matter.
"""

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal

from weather.pipeline.intake import Candidate

POLICY = "nearest-half-hour/v1"
HOUR = 3600
ANCHOR = Fraction(1800)
ON_TARGET = Fraction(60)

State = Literal["on_target", "off_target", "missing"]
# A capture's (JPEG bytes, RGB24 pixels), or the reject verdict for it ("decode", "changed").
Decode = Callable[[Candidate], tuple[bytes, bytes] | str]


@dataclass(frozen=True)
class Choice:
    state: State
    reason: str | None
    winner: Candidate | None
    # Exact source time minus the anchor, in seconds.
    offset: Fraction | None
    eligible: int
    rejections: dict[str, int] = field(default_factory=dict)
    jpeg: bytes | None = field(default=None, repr=False)
    rgb: bytes | None = field(default=None, repr=False)

    @property
    def offset_us(self) -> int | None:
        if self.offset is None:
            return None
        micros = self.offset * 1_000_000
        if micros.denominator != 1:
            raise ValueError("Source times are whole microseconds")
        return int(micros)


def order(candidate: Candidate, anchor: Fraction) -> tuple[Fraction, Fraction, str]:
    return abs(candidate.time - anchor), candidate.time, candidate.attempt_id


def choose(candidates: Iterable[Candidate], slot_start: int, decode: Decode) -> Choice:
    """Deterministic for a given set of sealed captures, whatever order they arrive in."""
    if slot_start % HOUR:
        raise ValueError("A slot starts at a whole UTC hour")
    start = Fraction(slot_start)
    end, anchor = start + HOUR, start + ANCHOR
    rejections: Counter[str] = Counter()
    eligible: list[Candidate] = []
    for candidate in candidates:
        if not start <= candidate.time < end:
            continue
        if candidate.reject is not None:
            rejections[candidate.reject] += 1
        else:
            eligible.append(candidate)
    for candidate in sorted(eligible, key=lambda item: order(item, anchor)):
        decoded = decode(candidate)
        if isinstance(decoded, str):
            rejections[decoded] += 1
            continue
        offset = candidate.time - anchor
        return Choice(
            state="on_target" if abs(offset) <= ON_TARGET else "off_target",
            reason=None,
            winner=candidate,
            offset=offset,
            eligible=len(eligible),
            rejections=dict(sorted(rejections.items())),
            jpeg=decoded[0],
            rgb=decoded[1],
        )
    return Choice(
        state="missing",
        reason="no_eligible_capture" if eligible or rejections else "no_capture",
        winner=None,
        offset=None,
        eligible=len(eligible),
        rejections=dict(sorted(rejections.items())),
    )
