# SPDX-License-Identifier: MIT
"""HMAC-signed export cursors bound to their filters. They provide integrity, not secrecy.

A cursor carries the keyset position (observed_at, id) and the received-at snapshot taken on
the first page, so rows that land later never shift an export. Its expiry slides with each page
but never passes an absolute cap from the first page.
"""

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .models import ObservationQuery, utc_instant, utc_text
from .registry import PublicError

SLIDING_SECONDS = 3600
ABSOLUTE_SECONDS = 86400


@dataclass(frozen=True)
class Position:
    snapshot: datetime
    after_time: datetime
    after_id: UUID
    issued_at: int
    expires_at: int


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("encoding")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def invalid_cursor() -> PublicError:
    return PublicError("invalid_cursor", "Invalid, expired or mismatched export cursor.")


def filters_digest(query: ObservationQuery) -> str:
    data = {
        "start": utc_text(query.start),
        "end": utc_text(query.end),
        "metrics": query.metrics,
        "limit": query.limit,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class CursorCodec:
    def __init__(
        self,
        key: bytes,
        *,
        sliding_seconds: int = SLIDING_SECONDS,
        absolute_seconds: int = ABSOLUTE_SECONDS,
    ) -> None:
        if len(key) < 32 or not 0 < sliding_seconds <= absolute_seconds:
            raise ValueError("Invalid cursor signing configuration")
        self._key = key
        self._sliding, self._absolute = sliding_seconds, absolute_seconds

    def _sign(self, payload: bytes) -> bytes:
        return hmac.digest(self._key, payload, "sha256")

    def encode(
        self,
        query: ObservationQuery,
        *,
        snapshot: datetime,
        after_time: datetime,
        after_id: UUID,
        now: datetime,
        previous: Position | None = None,
    ) -> str:
        epoch = int(now.timestamp())
        issued = previous.issued_at if previous else epoch
        payload = json.dumps(
            {
                "v": 1,
                "f": filters_digest(query),
                "s": utc_text(snapshot),
                "t": utc_text(after_time),
                "i": str(after_id),
                "a": issued,
                "x": min(epoch + self._sliding, issued + self._absolute),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return _b64(payload) + "." + _b64(self._sign(payload))

    def decode(self, token: str, query: ObservationQuery, *, now: datetime) -> Position:
        try:
            encoded, mac = token.split(".")
            payload, signature = _unb64(encoded), _unb64(mac)
            if not hmac.compare_digest(signature, self._sign(payload)):
                raise ValueError("signature")
            data = json.loads(payload)
            epoch = int(now.timestamp())
            if (
                data["v"] != 1
                or not hmac.compare_digest(str(data["f"]), filters_digest(query))
                or not isinstance(data["a"], int)
                or not isinstance(data["x"], int)
                or not data["a"] <= epoch < data["x"] <= data["a"] + self._absolute
            ):
                raise ValueError("binding or expiry")
            position = Position(
                snapshot=utc_instant(data["s"]),
                after_time=utc_instant(data["t"]),
                after_id=UUID(data["i"]),
                issued_at=data["a"],
                expires_at=data["x"],
            )
            if not query.start <= position.after_time < query.end:
                raise ValueError("position")
            return position
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise invalid_cursor() from None
