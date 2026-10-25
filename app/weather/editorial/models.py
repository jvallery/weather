# SPDX-License-Identifier: MIT
"""Code-first operator shapes (#44). OpenAPI and the editor's TypeScript are generated from these.

Inputs reject unknown fields, so an actor, author, evidence or measured-value field is a 422:
the server takes the actor from the allowlisted identity and never accepts a measurement. In a
PATCH, an omitted field is unchanged; an explicit `null` clears only `description` and `note`.
"""

import re
from datetime import date, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from weather.api.models import UTCInstant

MAX_DESCRIPTION = 8000
MAX_NOTE = 4000
MAX_TAGS = 32
MAX_RECEIPTS = 100
MAX_EVENT = timedelta(days=31)
# Plain text: tabs and newlines are allowed, other control characters never are.
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f  ]")


def _plain(value: str) -> str:
    if not value.strip():
        raise ValueError("Text must not be blank")
    if CONTROL.search(value):
        raise ValueError("Text must not contain control characters")
    return value


def _line(value: str) -> str:
    _plain(value)
    if "\n" in value or "\t" in value:
        raise ValueError("Text must be one line")
    return value


Description = Annotated[
    str, StringConstraints(min_length=1, max_length=MAX_DESCRIPTION), AfterValidator(_plain)
]
Note = Annotated[str, StringConstraints(min_length=1, max_length=MAX_NOTE), AfterValidator(_plain)]
Reason = Annotated[str, StringConstraints(min_length=1, max_length=2000), AfterValidator(_plain)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=240), AfterValidator(_line)]
TagName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9 _-]{0,47}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class OperatorModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=False,
        allow_inf_nan=False,
        hide_input_in_errors=True,
        json_schema_serialization_defaults_required=True,
    )


class Tag(OperatorModel):
    tag: TagName
    visibility: Literal["public", "private"]


class EventChange(OperatorModel):
    """An event correction through #41's override hook: label or visibility only."""

    label: Label | None = None
    visibility: Literal["public", "private", "withdrawn"] | None = None

    @model_validator(mode="after")
    def checked(self) -> EventChange:
        if not self.model_fields_set:
            raise ValueError("No event change")
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError("Event fields cannot be null")
        return self


class Changes(OperatorModel):
    description: Description | None = None
    tags: list[Tag] = Field(default_factory=list, max_length=MAX_TAGS)
    favorite: bool | None = None
    note: Note | None = None
    # Re-bind the human description (or event override) to the current media and evidence.
    rebase: Literal[True] | None = None
    event: EventChange | None = None

    @model_validator(mode="after")
    def checked(self) -> Changes:
        if not self.model_fields_set:
            raise ValueError("No changes")
        for name in ("favorite", "rebase", "event"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError("Only description and note can be cleared")
        names = [tag.tag for tag in self.tags]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate tag")
        return self


class SubjectPatch(OperatorModel):
    expected_revision: int = Field(ge=0, le=2**31 - 1)
    reason: Reason
    changes: Changes


class ManualEvent(OperatorModel):
    """A standalone manual annotation through #41's write_manual hook."""

    reason: Reason
    label: Label
    basis: Literal["instrument", "visual", "inference"]
    start: UTCInstant
    end: UTCInstant
    camera: Literal["skycam_east", "skycam_west", "frontyard_1"] | None = None
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def interval(self) -> ManualEvent:
        if not self.start < self.end:
            raise ValueError("start must precede end")
        if self.end - self.start > MAX_EVENT:
            raise ValueError("Interval too long")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Duplicate evidence")
        return self


class Evidence(OperatorModel):
    media_sha256: str | None
    evidence_hash: str | None


class DescriptionState(OperatorModel):
    """Old versus new evidence: what the human text was written against, and what is current."""

    human: str | None
    active: bool
    stale: bool
    effective: str | None
    automated: str | None
    bound: Evidence | None
    current: Evidence


class VideoSummary(OperatorModel):
    camera: str
    cycle_date: date
    segment: Literal["daytime", "overnight"]
    start: UTCInstant
    end: UTCInstant
    state: str
    withdrawn: bool


class EventRevision(OperatorModel):
    revision: int
    label: str | None
    visibility: Literal["public", "private", "withdrawn"]
    withdrawal_reason: str | None
    author_kind: str
    evidence_hash: str


class EventState(OperatorModel):
    kind: str
    origin: str
    basis: str
    camera: str | None
    start: UTCInstant
    end: UTCInstant
    current: EventRevision
    # The last operator revision, shown beside the current one when a rule withdrew it as stale.
    human: EventRevision | None
    stale: bool


class AuditEntry(OperatorModel):
    action: Literal["edit", "rebase", "manual_event"]
    actor: str
    recorded_at: UTCInstant
    reason: str
    old_revision: int
    new_revision: int


class Subject(OperatorModel):
    subject_id: UUID
    kind: Literal["video", "event"]
    revision: int
    updated_at: UTCInstant | None
    video: VideoSummary | None
    description: DescriptionState | None
    event: EventState | None
    tags: list[Tag]
    favorite: bool
    note: str | None
    audit: list[AuditEntry] = Field(max_length=20)


class Operator(OperatorModel):
    username: str


class ReceiptQuery(OperatorModel):
    start: UTCInstant
    end: UTCInstant
    family: (
        Literal[
            "obs_st",
            "rapid_wind",
            "evt_precip",
            "evt_strike",
            "device_status",
            "hub_status",
            "obs_air",
            "obs_sky",
            "unknown",
        ]
        | None
    ) = None
    limit: int = Field(default=MAX_RECEIPTS, ge=1, le=MAX_RECEIPTS)
    cursor: str | None = Field(default=None, max_length=2048)


class Receipt(OperatorModel):
    id: UUID
    received_at: UTCInstant
    family: str
    parse_state: Literal["parsed", "unsupported", "quarantined"]
    parse_reason: str | None
    parser_revision: str
    sha256: Sha256
    bytes: int
    bytes_base64: str


class Receipts(OperatorModel):
    generated_at: UTCInstant
    items: list[Receipt] = Field(max_length=MAX_RECEIPTS)
    next_cursor: str | None


class OperatorProblem(OperatorModel):
    code: str
    message: str
    current_revision: int | None = None
