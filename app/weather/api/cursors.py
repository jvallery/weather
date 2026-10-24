# SPDX-License-Identifier: MIT
"""Authenticated, bounded export continuation. Tokens provide integrity, not secrecy."""

import base64
import hashlib
import hmac
import json
import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, StringConstraints

from .metric_registry import REGISTRY_REVISION, PublicInputError
from .public_common import PublicModel, utc_instant
from .public_models import AggregateQuery, ObservationQuery


def watermark(value: str) -> str:
    if not re.fullmatch(r"0|[1-9][0-9]{0,18}", value) or int(value) > 9223372036854775807:
        raise ValueError("Invalid publication watermark")
    return value


def position(value: str) -> str:
    if not value or len(value.encode("utf8")) > 512 or any(ord(char) < 32 for char in value):
        raise ValueError("Invalid bounded source position")
    return value


Watermark = Annotated[str, AfterValidator(watermark)]
Position = Annotated[str, AfterValidator(position)]


class Snapshot(PublicModel):
    watermark: Watermark
    position: Position


class CursorState(Snapshot):
    version: Literal[1] = 1
    filters_digest: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    issued_at: Annotated[int, Field(ge=0, strict=True)]
    expires_at: Annotated[int, Field(gt=0, strict=True)]


def canonical_filters(query: ObservationQuery, policy_revision: str = REGISTRY_REVISION) -> bytes:
    """Equivalent UTC representations and metric ordering bind to the same query."""
    data = query.model_dump(mode="json", exclude={"cursor"})
    data["metrics"] = sorted(query.metrics)
    data["policy_revision"] = policy_revision
    data["schema_version"] = 1
    data["start"] = query.start.isoformat(timespec="microseconds")
    data["end"] = query.end.isoformat(timespec="microseconds")
    data["operation"] = "aggregates" if isinstance(query, AggregateQuery) else "observations"
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _base64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Noncanonical token encoding")
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _base64(decoded) != value:
        raise ValueError("Noncanonical token encoding")
    return decoded


def _invalid() -> PublicInputError:
    return PublicInputError("invalid_cursor", "Invalid, expired or mismatched export cursor.")


class CursorCodec:
    def __init__(
        self, key: bytes, *, ttl_seconds: int = 900, policy_revision: str = REGISTRY_REVISION
    ) -> None:
        if (
            not isinstance(key, bytes)
            or not 32 <= len(key) <= 128
            or type(ttl_seconds) is not int
            or not 1 <= ttl_seconds <= 86400
        ):
            raise ValueError("Invalid cursor signing configuration")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", policy_revision):
            raise ValueError("Invalid cursor policy revision")
        self._policy_revision = policy_revision
        self._key = key
        self.ttl_seconds = ttl_seconds

    def encode(
        self,
        query: ObservationQuery,
        snapshot: Snapshot,
        *,
        now: datetime,
        previous: CursorState | None = None,
    ) -> str:
        epoch = int(utc_instant(now).timestamp())
        state = CursorState(
            watermark=snapshot.watermark,
            position=snapshot.position,
            filters_digest=hashlib.sha256(
                canonical_filters(query, self._policy_revision)
            ).hexdigest(),
            issued_at=previous.issued_at if previous else epoch,
            expires_at=previous.expires_at if previous else epoch + self.ttl_seconds,
        )
        if state.expires_at <= epoch:
            raise _invalid()
        payload = state.model_dump_json().encode()
        if len(payload) > 1400:
            raise ValueError("Source cursor exceeds the bounded publication interface")
        signature = hmac.digest(self._key, payload, "sha256")
        token = _base64(payload) + "." + _base64(signature)
        if len(token) > 2048:
            raise ValueError("Export cursor exceeds the public interface limit")
        return token

    def decode(self, token: str, query: ObservationQuery, *, now: datetime) -> CursorState:
        try:
            if not isinstance(token, str) or len(token) > 2048 or token.count(".") != 1:
                raise ValueError("Invalid token length/shape")
            encoded, mac = token.split(".")
            payload, signature = _decode(encoded), _decode(mac)
            if (
                len(payload) > 1400
                or len(signature) != 32
                or not hmac.compare_digest(
                    signature,
                    hmac.digest(self._key, payload, "sha256"),
                )
            ):
                raise ValueError("Invalid token integrity")
            state = CursorState.model_validate_json(payload)
            epoch = int(utc_instant(now).timestamp())
            if (
                state.issued_at > epoch
                or state.expires_at <= epoch
                or not 0 < state.expires_at - state.issued_at <= self.ttl_seconds
                or not hmac.compare_digest(
                    state.filters_digest,
                    hashlib.sha256(canonical_filters(query, self._policy_revision)).hexdigest(),
                )
            ):
                raise ValueError("Invalid export binding/expiry")
            return state
        except ValueError, TypeError, UnicodeError:
            raise _invalid() from None
