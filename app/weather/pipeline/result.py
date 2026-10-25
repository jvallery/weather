# SPDX-License-Identifier: MIT
"""`RenderResult`: the validated, deterministic record of one segment render.

It carries the recipe (shutter, thresholds, FFmpeg build, image digest), the input-manifest
hash and every output's hash, and no wall-clock time, so a resumed render reproduces it byte
for byte. It never names a camera address, credential, bucket, object key or input frame hash.
"""

from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RENDITIONS = ("native", "web", "mobile")
EMBARGO = timedelta(seconds=630)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Rendition(Model):
    role: Literal["native", "web", "mobile"]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frames: int = Field(gt=0, le=7200)
    sha256: Hash
    bytes: int = Field(gt=0)


class Poster(Model):
    role: str = Field(pattern=r"^poster(_[a-z0-9]+)?$")
    extension: Literal["jpg", "webp"]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frame: int = Field(ge=0)
    sha256: Hash
    bytes: int = Field(gt=0)


class SlotCoverage(Model):
    expected: int = Field(ge=0)
    accepted: int = Field(ge=0)
    fraction: float = Field(ge=0, le=1)
    rejected: dict[str, int]
    missing: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    reported_losses: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.accepted + self.missing + sum(self.rejected.values()) < self.expected:
            raise ValueError("Slot accounting does not cover the grid")
        if self.accepted > self.expected:
            raise ValueError("More accepted slots than expected")
        return self


class Build(Model):
    """The image and FFmpeg build that rendered mezzanine frames [first, last)."""

    encoder: str = Field(max_length=128)
    image: str = Field(pattern=r"^(unpinned|sha256:[0-9a-f]{64})$")
    first: int = Field(ge=0)
    last: int = Field(gt=0)


class ExposureCoverage(Model):
    rendered: int = Field(ge=0)
    gaps: list[tuple[int, int]]
    threshold: str


class RenderResult(Model):
    schema_version: Literal[1] = 1
    camera: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    cycle_date: date
    segment: Literal["daytime", "overnight"]
    revision: int = Field(gt=0)
    frames: int = Field(gt=0, le=7200)
    starts_at: datetime
    ends_at: datetime
    public_not_before: datetime
    state: Literal["published", "partial", "unavailable"]
    reason: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    recipe: dict[str, Any]
    input_manifest_sha256: Hash
    inputs: int = Field(ge=0)
    mezzanine_builds: list[Build]
    slots: SlotCoverage
    exposure: ExposureCoverage
    renditions: list[Rendition]
    posters: list[Poster]
    sidecar_sha256: Hash
    expected_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.state == "published") != (self.reason is None):
            raise ValueError("Only published results have no reason")
        if not self.starts_at < self.ends_at or self.public_not_before < self.ends_at + EMBARGO:
            raise ValueError("Embargo ends before segment end + 630 s")
        gap_frames = sum(last - first for first, last in self.exposure.gaps)
        ordered = sorted(self.exposure.gaps)
        if (
            ordered != self.exposure.gaps
            or any(not 0 <= first < last <= self.frames for first, last in ordered)
            or any(a[1] >= b[0] for a, b in zip(ordered, ordered[1:], strict=False))
            or self.exposure.rendered + gap_frames != self.frames
        ):
            raise ValueError("Gap ranges must be ordered, disjoint and account for every frame")
        spans = [(b.first, b.last) for b in self.mezzanine_builds]
        if spans and (
            spans[0][0] != 0
            or spans[-1][1] != self.frames
            or any(a[1] != b[0] or a[0] >= a[1] for a, b in zip(spans, spans[1:], strict=False))
        ):
            raise ValueError("Mezzanine builds must cover every frame once, in order")
        if self.state == "unavailable":
            if self.renditions or self.posters:
                raise ValueError("Unavailable segments carry no media")
            return self
        if sorted(r.role for r in self.renditions) != sorted(RENDITIONS):
            raise ValueError("Exactly one native, web and mobile rendition")
        if any(r.frames != self.frames for r in self.renditions):
            raise ValueError("Every rendition has exactly N frames")
        if not any(p.role == "poster" for p in self.posters):
            raise ValueError("A poster is required")
        for poster in self.posters:
            if not poster.frame < self.frames or any(
                first <= poster.frame < last for first, last in self.exposure.gaps
            ):
                raise ValueError("A poster must be an observed (non-slate) frame")
        return self
