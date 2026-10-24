# SPDX-License-Identifier: MIT
"""Public scalar boundaries; no configuration, I/O, identities or raw receipt fields."""

import math
import re
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictFloat,
    StrictInt,
    StringConstraints,
    WithJsonSchema,
)

UTC_PATTERN = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$"


def utc_instant(value: Any) -> datetime:
    if isinstance(value, str):
        if not re.fullmatch(UTC_PATTERN, value):
            raise ValueError("An explicit UTC Z instant with microsecond precision is required")
        value = datetime.fromisoformat(value)
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        raise ValueError("An explicit UTC instant is required")
    return value.astimezone(UTC)


def finite(value: int | float) -> int | float:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Public numbers must be finite")
    return value


UTCInstant = Annotated[
    datetime,
    BeforeValidator(utc_instant),
    PlainSerializer(lambda value: value.isoformat().replace("+00:00", "Z"), return_type=str),
    WithJsonSchema({"type": "string", "format": "date-time", "pattern": UTC_PATTERN}),
]
Number = Annotated[StrictInt | StrictFloat, AfterValidator(finite)]
Nonnegative = Annotated[Number, Field(ge=0)]
Positive = Annotated[Number, Field(gt=0)]
FractionValue = Annotated[Number, Field(ge=0, le=1)]
OpaqueID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
MetricID = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Unit = Annotated[str, StringConstraints(min_length=1, max_length=32)]
Reason = Annotated[str, StringConstraints(min_length=1, max_length=512)]
Cursor = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,2048}$")]


class PublicModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        hide_input_in_errors=True,
        json_schema_serialization_defaults_required=True,
    )
