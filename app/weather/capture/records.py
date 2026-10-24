# SPDX-License-Identifier: MIT
"""Code-first private manifest v2, shared by the writer and strict chunk reader."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, model_validator


def _utc(value: str) -> str:
    datetime.fromisoformat(value)
    return value


Camera = Literal["skycam_east", "skycam_west", "frontyard_1"]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,88}$")]
UTCText = Annotated[
    str,
    Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"),
    AfterValidator(_utc),
]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NS = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,19})$")]
Failure = Literal[
    "timeout",
    "transport",
    "protocol",
    "authentication",
    "redirect",
    "http_status",
    "content_type",
    "oversized",
    "truncated",
    "jpeg_markers",
    "geometry",
    "decode_error",
    "overdue",
    "backoff",
    "staging_full",
    "persist_failed",
    "shutdown",
]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class Settings(Record):
    camera: Camera
    geometry: tuple[Annotated[int, Field(ge=1, le=16384)], Annotated[int, Field(ge=1, le=16384)]]
    cadence_ms: int = Field(ge=1000, le=3600000)
    phase_ms: int = Field(ge=0)
    chunk_seconds: int = Field(ge=1, le=300)
    connect_seconds: float = Field(gt=0, le=5)
    read_seconds: float = Field(gt=0, le=5)
    total_seconds: float = Field(gt=0, le=5)
    max_bytes: int = Field(ge=1, le=16777216)
    cache_allowance_ms: int | None = Field(default=None, ge=0)
    calibration_revision: Identifier | None = None

    @model_validator(mode="after")
    def valid_bounds(self) -> Settings:
        if self.phase_ms >= self.cadence_ms:
            raise ValueError("Phase exceeds cadence")
        if max(self.connect_seconds, self.read_seconds) > self.total_seconds:
            raise ValueError("Socket timeout exceeds total deadline")
        if self.cache_allowance_ms is not None and self.calibration_revision is None:
            raise ValueError("Cache allowance requires calibration")
        return self


class Header(Record):
    record_type: Literal["chunk_header"]
    schema_version: Literal[2]
    camera: Camera
    pod_uid: Identifier
    incarnation: Identifier
    sequence: int = Field(ge=0)
    config_hash: Hash
    slot_window_start_utc: UTCText
    slot_window_end_utc: UTCText
    settings: Settings

    @model_validator(mode="after")
    def valid_window_and_settings(self) -> Header:
        start = datetime.fromisoformat(self.slot_window_start_utc)
        end = datetime.fromisoformat(self.slot_window_end_utc)
        if not timedelta(0) < end - start <= timedelta(seconds=self.settings.chunk_seconds):
            raise ValueError("Invalid chunk window")
        if start.strftime("%Y-%m-%dT%H") != (end - timedelta(microseconds=1)).strftime(
            "%Y-%m-%dT%H"
        ):
            raise ValueError("Chunk window crosses UTC hour")
        content = json.dumps(self.settings.model_dump(), sort_keys=True, separators=(",", ":"))
        if (
            self.settings.camera != self.camera
            or hashlib.sha256(content.encode()).hexdigest() != self.config_hash
        ):
            raise ValueError("Header settings/hash differ")
        return self


class Member(Record):
    name: str = Field(pattern=r"^frames/[A-Za-z0-9][A-Za-z0-9_-]{0,88}\.jpg$")
    bytes: int = Field(gt=0, le=16777216)
    sha256: Hash
    width: int = Field(ge=1, le=16384)
    height: int = Field(ge=1, le=16384)


class Attempt(Record):
    record_type: Literal["capture_attempt"]
    attempt_id: Identifier
    slot_id: Identifier
    slot_at_utc: UTCText
    clock_epoch: Identifier
    camera: Camera
    status: Literal["captured", "skipped", "request_failed"]
    failure_class: Failure | None
    member: Member | None
    duplicate_hash: bool | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    request_started_at_utc: UTCText | None = None
    response_completed_at_utc: UTCText | None = None
    monotonic_started_ns: NS | None = None
    monotonic_completed_ns: NS | None = None
    source_at_utc: UTCText | None = None
    source_time_basis: Literal["request_midpoint", "calibrated_request_midpoint"] | None = None
    source_lower_bound: UTCText | None = None
    capture_upper_bound: UTCText | None = None
    uncertainty_ms: float | None = Field(default=None, ge=0)
    freshness: Literal["verified", "plausible", "suspected_stale", "unknown"] | None = None
    calibration_revision: Identifier | None = None
    clock_discontinuity: bool | None = None

    @model_validator(mode="after")
    def bound_success_and_time(self) -> Attempt:
        if self.status == "captured":
            if self.member is None or self.failure_class is not None:
                raise ValueError("Captured attempt requires a bound frame and no failure")
            if self.member.name != f"frames/{self.attempt_id}.jpg":
                raise ValueError("Attempt and frame identities differ")
            if any(
                x is None
                for x in [
                    self.request_started_at_utc,
                    self.response_completed_at_utc,
                    self.monotonic_started_ns,
                    self.monotonic_completed_ns,
                    self.source_at_utc,
                    self.source_time_basis,
                    self.freshness,
                ]
            ):
                raise ValueError("Captured attempt requires acquisition timing")
        elif self.member is not None or self.failure_class is None:
            raise ValueError("Failed/skipped attempt must contain a reason and no frame")
        if self.capture_upper_bound is not None and (
            self.source_lower_bound is None
            or self.uncertainty_ms is None
            or self.calibration_revision is None
            or self.clock_discontinuity
        ):
            raise ValueError("Finite timing requires calibration and continuous clock")
        if self.monotonic_started_ns is not None and self.monotonic_completed_ns is not None:
            if int(self.monotonic_completed_ns) < int(self.monotonic_started_ns):
                raise ValueError("Monotonic time reversed")
        return self


class LossRange(Record):
    record_type: Literal["loss_range"]
    failure_class: Literal["persist_failed"]
    first_slot_id: Identifier
    last_slot_id: Identifier
    first_slot_at_utc: UTCText
    last_slot_at_utc: UTCText
    attempt_count: int = Field(ge=1)
    individual_details_lost: Literal[True]


class UncertainRange(Record):
    record_type: Literal["unsealed_range"]
    classification: Literal["unsealed_uncertain", "seal_uncertain"]
    candidate_id: Hash | None = None
    start_at_utc: UTCText | None = None
    end_at_utc: UTCText | None = None
    inventory_overflow_count: int | None = Field(default=None, ge=1)
    individual_details_lost: bool
    reconciliation: str = Field(max_length=256)

    @model_validator(mode="after")
    def has_identity_or_overflow(self) -> UncertainRange:
        if self.candidate_id is None and self.inventory_overflow_count is None:
            raise ValueError("Uncertain range needs candidate identity or inventory overflow")
        return self


class SkippedRange(Record):
    record_type: Literal["skipped_range"]
    failure_class: Literal["overdue"]
    first_slot_id: Identifier
    last_slot_id: Identifier
    slot_id: Identifier
    slot_at_utc: UTCText
    last_slot_at_utc: UTCText
    slot_count: int = Field(ge=1)
    clock_epoch: Identifier
    status: Literal["skipped"]
    member: None


CaptureRecord = Annotated[
    Header | Attempt | LossRange | UncertainRange | SkippedRange,
    Field(discriminator="record_type"),
]
ADAPTER = TypeAdapter(CaptureRecord)


def validate_record(record: object) -> None:
    try:
        ADAPTER.validate_json(json.dumps(record, allow_nan=False))
    except ValueError, TypeError:
        raise ValueError("Invalid private capture manifest record") from None
