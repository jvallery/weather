# SPDX-License-Identifier: MIT
"""Preserve private evidence outside bounded JSONB storage without poisoning drain.

Exact datagram bytes remain authoritative. Tagged leaves are explicit evidence, never
canonical public metrics. Parsed envelope is SQL NULL when not representable as JSONB.
https://www.postgresql.org/docs/16/datatype-json.html
"""

from decimal import Decimal
from typing import Any

from .parse import json_text


def compatible(value: Any) -> bool:
    if isinstance(value, Decimal):
        exponent = value.as_tuple().exponent
        return (
            value.is_finite()
            and abs(value.adjusted()) <= 127
            and len(value.as_tuple().digits) <= 128
            and isinstance(exponent, int)
            and exponent >= -128
        )
    if isinstance(value, str):
        return not any(ord(char) == 0 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
    if isinstance(value, dict):
        return all(compatible(key) and compatible(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return all(compatible(item) for item in value)
    return True


def private_storage(value: Any) -> Any:
    if isinstance(value, dict):
        if any(not compatible(key) for key in value):
            return {"_weather_exact_private_json_text": json_text(value)}
        return {key: private_storage(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [private_storage(item) for item in value]
    if not compatible(value):
        return {"_weather_exact_private_json_text": json_text(value)}
    return value
