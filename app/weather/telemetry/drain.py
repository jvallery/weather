# SPDX-License-Identifier: MIT
"""Bounded CNPG drain: raw receipt plus typed family rows, one SAVEPOINT per receipt (P4)."""

import asyncio
import re
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .parse import FAMILIES, PARSER_REVISION, Packet, json_text, parse_packet
from .spool import Receipt, Spool

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# Public aliases only; serials stay in weather.device.private_id.
STATION_ALIAS, STATION_SITE = "home", "local"
DEVICE_VALID_FROM = datetime(2020, 1, 1, tzinfo=UTC)
# Receipts the receiver quarantined at admission are never projected, even on reproject.
ADMISSION_QUARANTINE = "missing_hub_identity"
# Connection, transaction-conflict, resource, operator-intervention, lock-timeout and privilege
# failures are not the receipt's fault: they fail the batch so the spool retries it unchanged.
SYSTEMIC_CLASSES, SYSTEMIC_CODES = ("08", "40", "53", "57"), ("42501", "55P03")


def sqlstate(error: BaseException) -> str | None:
    code = getattr(getattr(error, "orig", None), "sqlstate", None)
    return code if isinstance(code, str) and re.fullmatch(r"[0-9A-Z]{5}", code) else None


def systemic(error: BaseException) -> bool:
    if getattr(error, "connection_invalidated", False):
        return True
    code = sqlstate(error)
    return code is not None and (code[:2] in SYSTEMIC_CLASSES or code in SYSTEMIC_CODES)


def instant(epoch: Decimal) -> datetime | None:
    micros = epoch * 1_000_000
    if not micros.is_finite() or micros != micros.to_integral_value():
        # Do not silently collapse distinct protocol timestamps into one PG microsecond.
        return None
    try:
        return EPOCH + timedelta(microseconds=int(micros))
    except OverflowError, ValueError:
        return None


async def seed(db: AsyncConnection, hub: str, devices: frozenset[str]) -> None:
    """Idempotently insert the station and hub/ST device rows named by the mounted Secret."""
    await db.execute(
        text("INSERT INTO weather.station(alias,site) VALUES(:alias,:site) ON CONFLICT DO NOTHING"),
        {"alias": STATION_ALIAS, "site": STATION_SITE},
    )
    station = (
        await db.execute(
            text("SELECT id FROM weather.station WHERE alias=:alias"), {"alias": STATION_ALIAS}
        )
    ).scalar_one()
    known = dict(
        (await db.execute(text("SELECT private_id,kind FROM weather.device"))).tuples().all()
    )
    for serial, kind in [(hub, "hub"), *((serial, "tempest") for serial in sorted(devices))]:
        if serial in known:
            continue
        # Aliases are ordinal per kind, so adding a device never renames an existing one.
        ordinal = sum(1 for value in known.values() if value == kind) + 1
        await db.execute(
            text("""
            INSERT INTO weather.device(station_id,alias,private_id,kind,valid_from)
            VALUES(:station,:alias,:serial,:kind,:valid_from) ON CONFLICT DO NOTHING
        """),
            {
                "station": station,
                "alias": kind if ordinal == 1 else f"{kind}-{ordinal}",
                "serial": serial,
                "kind": kind,
                "valid_from": DEVICE_VALID_FROM,
            },
        )
        known[serial] = kind


async def store(db: AsyncConnection, receipt: Receipt, state: str, reason: str | None) -> bool:
    """Insert the raw receipt; False means this receipt ID is already stored (replay)."""
    inserted = (
        await db.execute(
            text("""
        INSERT INTO weather.telemetry_receipt
          (id,received_at,raw_bytes,sha256,parse_state,parse_reason,parser_revision,
           receiver_incarnation)
        VALUES(:id,:received,:raw,:sha,:state,:reason,:parser,:incarnation)
        ON CONFLICT(id) DO NOTHING RETURNING id
    """),
            {
                "id": receipt.id,
                "received": EPOCH + timedelta(microseconds=receipt.received_ns // 1000),
                "raw": receipt.raw,
                "sha": receipt.sha256,
                "state": state,
                "reason": reason,
                "parser": PARSER_REVISION,
                "incarnation": receipt.receiver_incarnation,
            },
        )
    ).first()
    return inserted is not None


async def project(
    db: AsyncConnection, receipt_id: uuid.UUID, packet: Packet, counts: Counter[str]
) -> int:
    """Insert typed family rows with ON CONFLICT DO NOTHING; the first receipt's row wins."""
    serial = packet.envelope.get("serial_number") if packet.envelope else None
    projected = 0
    for row in packet.rows:
        if row.family not in FAMILIES:
            raise ValueError("Unsupported canonical family")
        source = instant(row.source_ts) if row.source_ts is not None else None
        device = None
        if source is not None and row.quarantine_reason is None and isinstance(serial, str):
            device = (
                await db.execute(
                    text("""
                SELECT id FROM weather.device WHERE private_id=:private
                AND valid_from<=:source AND (valid_until IS NULL OR :source<valid_until)
            """),
                    {"private": serial, "source": source},
                )
            ).scalar_one_or_none()
        if device is None:
            # Invalid source time or no seeded device: raw is retained; reproject can retry.
            counts["unprojected"] += 1
            continue
        inserted = (
            await db.execute(
                text(f"""
            INSERT INTO weather.{row.family}
              (device_id,receipt_id,message_type,source_ts,row_idx,"values",quality)
            VALUES(:device,:receipt,:family,:source,:idx,CAST(:values AS jsonb),
                   CAST(:quality AS jsonb))
            ON CONFLICT(device_id,message_type,source_ts,row_idx) DO NOTHING RETURNING id
        """),
                {
                    "device": device,
                    "receipt": receipt_id,
                    "family": row.family,
                    "source": source,
                    "idx": row.ordinal,
                    "values": json_text(row.values),
                    "quality": json_text(row.quality),
                },
            )
        ).first()
        projected += int(inserted is not None)
    return projected


class Drain:
    def __init__(
        self,
        spool: Spool,
        engine: AsyncEngine,
        future_seconds: int = 120,
        hub_identity: str | None = None,
        device_identities: frozenset[str] = frozenset(),
    ) -> None:
        if not 0 <= future_seconds <= 86400:
            raise ValueError("Invalid future timestamp tolerance")
        self.spool, self.engine, self.future_seconds = spool, engine, future_seconds
        self.hub_identity, self.device_identities = hub_identity, device_identities
        self.seeded = hub_identity is None
        self.counts: Counter[str] = Counter()
        self.last_commit_ns: int | None = None
        self.last_error_code: str | None = None

    def _error(self, error: Exception) -> None:
        self.last_error_code = sqlstate(error) or "UNAVAILABLE"
        self.counts["drain_errors"] += 1

    async def once(self) -> int:
        # Counters merge only after the CNPG commit, so retries and rolled-back work never count.
        pending: Counter[str] = Counter()
        try:
            async with asyncio.timeout(30):
                if not self.seeded and self.hub_identity is not None:
                    async with self.engine.begin() as db:
                        await seed(db, self.hub_identity, self.device_identities)
                    self.seeded = True
                batch = await asyncio.to_thread(self.spool.batch)
                if not batch:
                    return 0
                async with self.engine.begin() as db:
                    for item in batch:
                        receipt_counts: Counter[str] = Counter()
                        try:
                            async with db.begin_nested():
                                await self._receipt(db, item.receipt, receipt_counts)
                        except Exception as error:
                            if systemic(error):
                                raise
                            # A deterministic failure must not block later receipts forever:
                            # keep its raw bytes as a quarantined row and move on.
                            reason = item.receipt.quarantine_reason or "drain_failed"
                            async with db.begin_nested():
                                new = int(await store(db, item.receipt, "quarantined", reason))
                            receipt_counts = Counter(committed=new, drain_quarantined=new)
                        pending.update(receipt_counts)
        except Exception as error:
            # Never log exceptions: DB errors may include parameters/raw/private identities.
            self._error(error)
            return 0
        # Known committed counters advance before the separately fallible local acknowledgement.
        self.counts.update(pending)
        self.last_commit_ns = time.time_ns()
        try:
            await asyncio.to_thread(self.spool.acknowledge, batch)
        except Exception:
            self.counts["spool_ack_errors"] += 1
            return 0
        return len(batch)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if await self.once() == 0:
                try:
                    await asyncio.wait_for(stop.wait(), 1)
                except TimeoutError:
                    pass

    async def _receipt(self, db: AsyncConnection, receipt: Receipt, counts: Counter[str]) -> None:
        if receipt.quarantine_reason is not None:
            # Admission quarantine is raw-only and never reaches the parser.
            counts["committed"] += int(
                await store(db, receipt, "quarantined", receipt.quarantine_reason)
            )
            return
        packet = parse_packet(
            receipt.raw, Decimal(receipt.received_ns) / 1_000_000_000, self.future_seconds
        )
        if await store(db, receipt, packet.state, packet.reason):
            counts["committed"] += 1
            counts["projected"] += await project(db, receipt.id, packet, counts)


async def reproject(
    engine: AsyncEngine,
    since: datetime,
    future_seconds: int = 120,
    page: int = 500,
) -> tuple[int, int, Counter[str]]:
    """Rebuild typed rows from retained raw bytes; existing rows are never changed."""
    counts: Counter[str] = Counter()
    receipts = rows = 0
    cursor: tuple[datetime, uuid.UUID] = (since, uuid.UUID(int=0))
    while True:
        async with asyncio.timeout(60), engine.begin() as db:
            batch = (
                await db.execute(
                    text("""
                SELECT id,received_at,raw_bytes FROM weather.telemetry_receipt
                WHERE (received_at,id)>(:at,:id) AND coalesce(parse_reason,'')<>:skip
                ORDER BY received_at,id LIMIT :page
            """),
                    {
                        "at": cursor[0],
                        "id": cursor[1],
                        "skip": ADMISSION_QUARANTINE,
                        "page": page,
                    },
                )
            ).all()
            for item in batch:
                micros = (item.received_at - EPOCH) // timedelta(microseconds=1)
                packet = parse_packet(
                    item.raw_bytes, Decimal(micros) / 1_000_000, future_seconds
                )
                receipt_counts: Counter[str] = Counter()
                new = 0
                try:
                    async with db.begin_nested():
                        new = await project(db, item.id, packet, receipt_counts)
                except Exception as error:
                    if systemic(error):
                        raise
                    new, receipt_counts = 0, Counter(reproject_errors=1)
                rows += new
                counts.update(receipt_counts)
            receipts += len(batch)
        if len(batch) < page:
            return receipts, rows, counts
        cursor = (batch[-1].received_at, batch[-1].id)
