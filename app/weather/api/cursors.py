# SPDX-License-Identifier: MIT
"""HMAC-signed export cursors bound to their filters. They provide integrity, not secrecy.

A cursor carries the keyset position (observed_at, id) and the database insert-time snapshot taken
on the first page, so rows that land later never shift an export. Its expiry slides with each page
but never passes an absolute cap from the first page.

Event feed cursors (#41) carry (source start, event id) and the first page's exact PostgreSQL MVCC
snapshot (pg_snapshot text), so concurrent inserts and revisions never repeat or drop an event.
"""

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from .models import AlmanacQuery, EventQuery, ObservationQuery, utc_instant, utc_text
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


@dataclass(frozen=True)
class EventPosition:
    snapshot: str
    after_start: datetime
    after_id: UUID
    issued_at: int
    expires_at: int


# xmin:xmax:xip-list. The in-progress list is tiny in practice; a cursor stays URL-sized.
SNAPSHOT_PATTERN = r"[0-9]{1,20}:[0-9]{1,20}:(?:[0-9]{1,20}(?:,[0-9]{1,20}){0,63})?"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("encoding")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def seal(key: bytes, payload: dict[str, object]) -> str:
    """Sign a small JSON position. Shared by every cursor in this API."""
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return _b64(data) + "." + _b64(hmac.digest(key, data, "sha256"))


def unseal(key: bytes, token: str) -> dict[str, object]:
    """Verify and decode a sealed position, or raise invalid_cursor()."""
    try:
        encoded, mac = token.split(".")
        data = _unb64(encoded)
        if not hmac.compare_digest(_unb64(mac), hmac.digest(key, data, "sha256")):
            raise ValueError("signature")
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("shape")
        return payload
    except ValueError, UnicodeError:
        raise invalid_cursor() from None


def invalid_cursor() -> PublicError:
    return PublicError("invalid_cursor", "Invalid, expired or mismatched export cursor.")


# Almanac pages reuse the same signed token: after_id carries the next item offset.
Query = ObservationQuery | AlmanacQuery


def filters_digest(query: Query) -> str:
    data = query.cursor_filters()
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def event_filters_digest(query: EventQuery) -> str:
    data = {
        "kind": "events",
        "day": query.day.isoformat() if query.day else None,
        "camera": query.camera,
        "origin": query.origin,
        "order": query.order,
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

    def _seal(self, data: dict[str, object], now: datetime, issued_at: int | None) -> str:
        epoch = int(now.timestamp())
        issued = epoch if issued_at is None else issued_at
        payload = json.dumps(
            {**data, "a": issued, "x": min(epoch + self._sliding, issued + self._absolute)},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return _b64(payload) + "." + _b64(self._sign(payload))

    def _open(self, token: str, digest: str, now: datetime) -> dict[str, object]:
        encoded, mac = token.split(".")
        payload, signature = _unb64(encoded), _unb64(mac)
        if not hmac.compare_digest(signature, self._sign(payload)):
            raise ValueError("signature")
        data = json.loads(payload)
        epoch = int(now.timestamp())
        if (
            data["v"] != 1
            or not hmac.compare_digest(str(data["f"]), digest)
            or not isinstance(data["a"], int)
            or not isinstance(data["x"], int)
            or not data["a"] <= epoch < data["x"] <= data["a"] + self._absolute
        ):
            raise ValueError("binding or expiry")
        return cast(dict[str, object], data)

    def encode_events(
        self,
        query: EventQuery,
        *,
        snapshot: str,
        after_start: datetime,
        after_id: UUID,
        now: datetime,
        previous: EventPosition | None = None,
    ) -> str:
        if not re.fullmatch(SNAPSHOT_PATTERN, snapshot):
            raise PublicError("unavailable", "Weather data is temporarily unavailable.")
        data: dict[str, object] = {
            "v": 1,
            "f": event_filters_digest(query),
            "s": snapshot,
            "t": utc_text(after_start),
            "i": str(after_id),
        }
        return self._seal(data, now, previous.issued_at if previous else None)

    def decode_events(self, token: str, query: EventQuery, *, now: datetime) -> EventPosition:
        try:
            data = self._open(token, event_filters_digest(query), now)
            snapshot = str(data["s"])
            if not re.fullmatch(SNAPSHOT_PATTERN, snapshot):
                raise ValueError("snapshot")
            return EventPosition(
                snapshot=snapshot,
                after_start=utc_instant(data["t"]),
                after_id=UUID(str(data["i"])),
                issued_at=cast(int, data["a"]),
                expires_at=cast(int, data["x"]),
            )
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise invalid_cursor() from None

    def encode(
        self,
        query: Query,
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

    def decode(self, token: str, query: Query, *, now: datetime) -> Position:
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
