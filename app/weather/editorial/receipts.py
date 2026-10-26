# SPDX-License-Identifier: MIT
"""Private raw receipts (#44): exact stored bytes, their SHA-256 and parse lineage.

`GET /api/v1/operator/telemetry/receipts?start&end&family&cursor` serves at most 100 receipts
per page over a window of at most 24 hours, ordered by (received_at, id). Cursors are signed
with #14's CURSOR_HMAC_KEY, bound to the filters and to the first page's insert-time snapshot
(the database clock minus 120 s), so receipts that land later, such as a spool drain with an
old `received_at`, never repeat or shift an export. Bytes are re-hashed before they are served.
The payload carries private device and hub identifiers: it is never logged or cached.
"""

import base64
import hashlib
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from weather.api.cursors import invalid_cursor, seal, unseal
from weather.api.registry import PublicError
from weather.telemetry.parse import decode_envelope

from .models import Receipt, ReceiptQuery, Receipts

MAX_WINDOW = timedelta(hours=24)
# Rows read per request while filtering by family; a sparse family may return a short page.
SCAN = 1000
SLIDING_SECONDS = 3600
ABSOLUTE_SECONDS = 86400
FIRST = UUID(int=0)
SNAPSHOT_SQL = "SELECT clock_timestamp() - interval '120 seconds'"
PAGE_SQL = """SELECT id,received_at,raw_bytes,sha256,parse_state,parse_reason,parser_revision
FROM weather.telemetry_receipt
WHERE received_at >= :start AND received_at < :end AND inserted_at <= :snapshot
  AND (received_at,id) > (:after_time,:after_id)
ORDER BY received_at,id LIMIT :scan"""


class ReceiptIntegrityError(RuntimeError):
    """Stored bytes no longer hash to their recorded SHA-256."""


def _filters(query: ReceiptQuery) -> str:
    bound = f"{query.start.isoformat()}|{query.end.isoformat()}|{query.family}|{query.limit}"
    return hashlib.sha256(bound.encode()).hexdigest()[:32]


def check_window(query: ReceiptQuery) -> None:
    if not query.start < query.end:
        raise PublicError("invalid_request", "start must precede end.")
    if query.end - query.start > MAX_WINDOW:
        raise PublicError("range_too_large", "The receipts window is at most 24 hours.")


def _open(key: bytes, query: ReceiptQuery, token: str) -> tuple[datetime, datetime, UUID, int]:
    payload = unseal(key, token)
    try:
        if payload["k"] != "receipts" or payload["q"] != _filters(query):
            raise ValueError("filters")
        expires, issued = int(str(payload["x"])), int(str(payload["i"]))
        if time.time() > expires:
            raise ValueError("expired")
        return (
            datetime.fromisoformat(str(payload["s"])),
            datetime.fromisoformat(str(payload["t"])),
            UUID(str(payload["a"])),
            issued,
        )
    except KeyError, TypeError, ValueError:
        raise invalid_cursor() from None


def _seal(
    key: bytes, query: ReceiptQuery, snapshot: datetime, at: datetime, after: UUID, issued: int
) -> str:
    now = int(time.time())
    return seal(
        key,
        {
            "k": "receipts",
            "q": _filters(query),
            "s": snapshot.isoformat(),
            "t": at.isoformat(),
            "a": str(after),
            "i": issued,
            "x": min(now + SLIDING_SECONDS, issued + ABSOLUTE_SECONDS),
        },
    )


async def page(connection: AsyncConnection, key: bytes, query: ReceiptQuery) -> Receipts:
    check_window(query)
    if query.cursor:
        snapshot, after_time, after_id, issued = _open(key, query, query.cursor)
    else:
        snapshot = (await connection.execute(text(SNAPSHOT_SQL))).scalar_one()
        after_time, after_id, issued = query.start, FIRST, int(time.time())
    rows: list[Any] = list(
        (
            await connection.execute(
                text(PAGE_SQL),
                {
                    "start": query.start,
                    "end": query.end,
                    "snapshot": snapshot,
                    "after_time": after_time,
                    "after_id": after_id,
                    "scan": SCAN,
                },
            )
        ).mappings()
    )
    items: list[Receipt] = []
    position: tuple[datetime, UUID] | None = None
    for row in rows:
        raw = bytes(row["raw_bytes"])
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ReceiptIntegrityError("Receipt digest mismatch")
        position = (row["received_at"], row["id"])
        family = decode_envelope(raw).family
        if query.family is not None and family != query.family:
            continue
        items.append(
            Receipt(
                id=row["id"],
                received_at=row["received_at"],
                family=family,
                parse_state=row["parse_state"],
                parse_reason=row["parse_reason"],
                parser_revision=row["parser_revision"],
                sha256=row["sha256"],
                bytes=len(raw),
                bytes_base64=base64.b64encode(raw).decode("ascii"),
            )
        )
        if len(items) == query.limit:
            break
    exhausted = len(rows) < SCAN and (position is None or position == _last(rows))
    cursor = None
    if position is not None and not exhausted:
        cursor = _seal(key, query, snapshot, position[0], position[1], issued)
    return Receipts(generated_at=datetime.now(UTC), items=items, next_cursor=cursor)


def _last(rows: list[Any]) -> tuple[datetime, UUID] | None:
    return (rows[-1]["received_at"], rows[-1]["id"]) if rows else None
