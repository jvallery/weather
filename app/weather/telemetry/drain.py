# SPDX-License-Identifier: MIT
"""Bounded CNPG drain; replay retains receipt identity, receive times and raw bytes."""

import asyncio
import hashlib
import json
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .coverage import Cadence, DiagnosticInterval, arrival_interval
from .jsonb import compatible, private_storage
from .parse import FAMILIES, PARSER_REVISION, Packet, Row, json_text, parse_packet
from .reconcile import disputed_fields
from .spool import Receipt, Spool

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def instant(epoch: Decimal) -> datetime | None:
    micros = epoch * 1_000_000
    if not micros.is_finite() or micros != micros.to_integral_value():
        # Do not silently collapse distinct protocol timestamps into one PG microsecond.
        return None
    try:
        return EPOCH + timedelta(microseconds=int(micros))
    except OverflowError, ValueError:
        return None


def encoded(value: Any) -> str:
    return json_text(private_storage(value))


class Drain:
    def __init__(self, spool: Spool, engine: AsyncEngine, future_seconds: int = 120) -> None:
        if not 0 <= future_seconds <= 86400:
            raise ValueError("Invalid future timestamp tolerance")
        self.spool, self.engine, self.future_seconds = spool, engine, future_seconds
        self.counts: Counter[str] = Counter()
        self.last_commit_ns: int | None = None

    async def once(self) -> int:
        try:
            batch = await asyncio.to_thread(self.spool.batch)
            stats = await asyncio.to_thread(self.spool.stats)
            if not batch and not stats["losses"]:
                return 0
            async with asyncio.timeout(30), self.engine.begin() as connection:
                committed = projected = 0
                for item in batch:
                    new_receipt, new_rows = await self._receipt(connection, item.receipt)
                    committed += new_receipt
                    projected += new_rows
                await self._loss_snapshots(connection, stats)
        except Exception:
            self.counts["drain_errors"] += 1
            # Never log exceptions: DB errors may include parameters/raw/private identities.
            return 0
        # Known committed counters advance before the separately fallible local acknowledgement.
        self.counts["committed"] += committed
        self.counts["projected"] += projected
        self.last_commit_ns = time.time_ns()
        try:
            await asyncio.to_thread(self.spool.acknowledge, batch)
        except Exception:
            self.counts["spool_ack_errors"] += 1
            return 0
        return len(batch)

    async def _loss_snapshots(self, db: AsyncConnection, stats: dict[str, Any]) -> None:
        for loss in stats["losses"]:
            evidence = {"generation": stats["generation"], **loss}
            digest = hashlib.sha256(json_text(evidence).encode()).hexdigest()
            await db.execute(
                text("""
                INSERT INTO weather.telemetry_loss_snapshot
                  (generation,reason,first_ns,last_ns,lost_count,snapshot_sha256)
                VALUES(:generation,:reason,:first,:last,:count,:sha)
                ON CONFLICT(snapshot_sha256) DO NOTHING
            """),
                {
                    "generation": uuid.UUID(stats["generation"]),
                    "reason": loss["reason"],
                    "first": int(loss["first_ns"]),
                    "last": int(loss["last_ns"]),
                    "count": loss["count"],
                    "sha": digest,
                },
            )

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if await self.once() == 0:
                try:
                    await asyncio.wait_for(stop.wait(), 1)
                except TimeoutError:
                    pass

    async def _receipt(self, db: AsyncConnection, receipt: Receipt) -> tuple[int, int]:
        packet = parse_packet(
            receipt.raw, Decimal(receipt.received_ns) / 1_000_000_000, self.future_seconds
        )
        received_at = EPOCH + timedelta(microseconds=receipt.received_ns // 1000)
        inserted = (
            await db.execute(
                text("""
            INSERT INTO weather.telemetry_receipt
              (id,received_at,raw_bytes,parsed_json,sha256,parse_state,parser_revision,receiver_incarnation)
            VALUES(:id,:received,:raw,CAST(:parsed AS jsonb),:sha,:state,:parser,:incarnation)
            ON CONFLICT(id) DO NOTHING RETURNING id
        """),
                {
                    "id": receipt.id,
                    "received": received_at,
                    "raw": receipt.raw,
                    "parsed": json_text(packet.envelope)
                    if compatible(packet.envelope) and packet.envelope is not None
                    else None,
                    "sha": receipt.sha256,
                    "state": packet.state,
                    "parser": PARSER_REVISION,
                    "incarnation": receipt.receiver_incarnation,
                },
            )
        ).first()
        if inserted is None:
            await self._check_replay(db, receipt, received_at)
            return 0, 0
        private_id = packet.envelope.get("serial_number") if packet.envelope else None
        device = None
        if isinstance(private_id, str) and compatible(private_id):
            device = (
                await db.execute(
                    text("SELECT id FROM weather.device WHERE private_id=:private"),
                    {"private": private_id},
                )
            ).scalar_one_or_none()
        await db.execute(
            text("""
            INSERT INTO weather.receipt_transport
              (receipt_id,metadata,metadata_sha256,family,device_id,cadence)
            VALUES(:id,CAST(:metadata AS jsonb),:sha,:family,:device,CAST(:cadence AS jsonb))
        """),
            {
                "id": receipt.id,
                "metadata": receipt.metadata_json,
                "sha": hashlib.sha256(receipt.metadata_json.encode()).hexdigest(),
                "family": packet.family,
                "device": device,
                "cadence": encoded(self._packet_cadence(packet)),
            },
        )
        await self._coverage(db, receipt, packet, device)
        rows = 0
        for row in packet.rows:
            rows += await self._row(db, receipt, packet, row, private_id, received_at)
        return 1, rows

    @staticmethod
    def _packet_cadence(packet: Packet) -> dict[str, Any]:
        intervals = [
            Decimal(str(row.fields["report_interval"].canonical)) * 60
            for row in packet.rows
            if "report_interval" in row.fields
            and row.fields["report_interval"].state == "valid"
            and Decimal(str(row.fields["report_interval"].canonical)) * 60 <= 86400
        ]
        return {
            "seconds": max(intervals) if intervals else None,
            "state": "normal" if intervals else "unknown",
            "policy": "reported-interval-transport-1",
        }

    async def _coverage(
        self, db: AsyncConnection, receipt: Receipt, packet: Packet, device: uuid.UUID | None
    ) -> None:
        evidence = self._packet_cadence(packet)
        cadence = Cadence(evidence["seconds"], evidence["state"])
        intervals = []
        if packet.state == "quarantined" or any(row.quarantine_reason for row in packet.rows):
            intervals.append(
                DiagnosticInterval(
                    receipt.received_ns, receipt.received_ns, "parse_failure", "parser-receipt-1"
                )
            )
        if device is not None:
            previous = (
                await db.execute(
                    text("""
                SELECT metadata,cadence::text AS cadence_json FROM weather.receipt_transport
                WHERE device_id=:device AND family=:family AND receipt_id<>:receipt
                  AND metadata->>'receiver_incarnation'=:incarnation
                  AND (metadata->>'monotonic_ns')::bigint<:mono
                ORDER BY (metadata->>'monotonic_ns')::bigint DESC LIMIT 1
            """),
                    {
                        "device": device,
                        "family": packet.family,
                        "receipt": receipt.id,
                        "incarnation": receipt.receiver_incarnation,
                        "mono": receipt.monotonic_ns,
                    },
                )
            ).first()
            if previous is not None:
                prior = previous.metadata
                wall_delta = receipt.received_ns - int(prior["received_ns"])
                mono_delta = receipt.monotonic_ns - int(prior["monotonic_ns"])
                if abs(wall_delta - mono_delta) <= 1_000_000_000:
                    old = json.loads(previous.cadence_json, parse_float=Decimal, parse_int=Decimal)
                    old_cadence = Cadence(old["seconds"], old["state"])
                    if (
                        cadence.seconds is not None
                        and old_cadence.seconds is not None
                        and cadence.seconds > old_cadence.seconds
                    ):
                        cadence = Cadence(cadence.seconds, "reduced")
                    gap = arrival_interval(
                        int(prior["received_ns"]), receipt.received_ns, old_cadence, cadence
                    )
                    if gap is not None:
                        intervals.append(gap)
                else:
                    intervals.append(
                        DiagnosticInterval(
                            min(int(prior["received_ns"]), receipt.received_ns),
                            max(int(prior["received_ns"]), receipt.received_ns),
                            "receiver_clock_discontinuity",
                            "receive-clock-delta-1",
                            True,
                        )
                    )
        now_ns = time.time_ns()
        if now_ns - receipt.received_ns > 120_000_000_000:
            intervals.append(
                DiagnosticInterval(
                    receipt.received_ns, now_ns, "db_backlog", "receive-to-drain-120s-1"
                )
            )
        for interval in intervals:
            await db.execute(
                text("""
                INSERT INTO weather.telemetry_coverage_interval
                  (receipt_id,device_id,family,starts_ns,ends_ns,reason,policy,lost_count)
                VALUES(:receipt,:device,:family,:start,:end,:reason,:policy,:count)
                ON CONFLICT(receipt_id,family,starts_ns,ends_ns,reason) DO NOTHING
            """),
                {
                    "receipt": receipt.id,
                    "device": device,
                    "family": packet.family,
                    "start": interval.start_ns,
                    "end": interval.end_ns,
                    "reason": interval.reason,
                    "policy": interval.policy,
                    "count": None,
                },
            )

    async def _check_replay(
        self, db: AsyncConnection, receipt: Receipt, received_at: datetime
    ) -> None:
        existing = (
            await db.execute(
                text("""
            SELECT r.sha256,r.raw_bytes,t.metadata FROM weather.telemetry_receipt r
            LEFT JOIN weather.receipt_transport t ON t.receipt_id=r.id WHERE r.id=:id
        """),
                {"id": receipt.id},
            )
        ).one()
        if (
            existing.sha256 == receipt.sha256
            and existing.raw_bytes == receipt.raw
            and existing.metadata == receipt.metadata
        ):
            return
        await db.execute(
            text("""
            INSERT INTO weather.receipt_integrity_incident
              (receipt_id,alternate_raw,alternate_sha256,alternate_metadata,
               alternate_metadata_sha256,received_at)
            VALUES(:id,:raw,:sha,CAST(:metadata AS jsonb),:metadata_sha,:received)
            ON CONFLICT(receipt_id,alternate_sha256,alternate_metadata_sha256) DO NOTHING
        """),
            {
                "id": receipt.id,
                "raw": receipt.raw,
                "sha": receipt.sha256,
                "metadata": receipt.metadata_json,
                "metadata_sha": hashlib.sha256(receipt.metadata_json.encode()).hexdigest(),
                "received": received_at,
            },
        )
        self.counts["integrity_incident_attempts"] += 1

    async def _row(
        self,
        db: AsyncConnection,
        receipt: Receipt,
        packet: Packet,
        row: Row,
        private_id: Any,
        received_at: datetime,
    ) -> int:
        if row.family not in FAMILIES:
            raise ValueError("Unsupported canonical family")
        source_at = instant(row.source_ts) if row.source_ts is not None else None
        reason = row.quarantine_reason
        if source_at is None:
            reason = reason or "source_time_precision_unsupported"
        device = None
        if source_at is not None and isinstance(private_id, str) and compatible(private_id):
            device = (
                await db.execute(
                    text("""
                SELECT id FROM weather.device WHERE private_id=:private
                AND valid_from<=:source AND (valid_until IS NULL OR :source<valid_until)
            """),
                    {"private": private_id, "source": source_at},
                )
            ).scalar_one_or_none()
        if device is None:
            reason = reason or "device_validity_unqualified"
        canonical = None
        new_row = 0
        if reason is None:
            canonical = (
                await db.execute(
                    text(f"""
                INSERT INTO weather.{row.family}
                  (id,device_id,receipt_id,message_type,source_ts,row_idx,"values",quality)
                VALUES(:id,:device,:receipt,:family,:source,:idx,CAST(:values AS jsonb),
                       CAST(:quality AS jsonb))
                ON CONFLICT(device_id,message_type,source_ts,row_idx) DO NOTHING RETURNING id
            """),
                    {
                        "id": uuid.uuid4(),
                        "device": device,
                        "receipt": receipt.id,
                        "family": row.family,
                        "source": source_at,
                        "idx": row.ordinal,
                        "values": encoded(row.values),
                        "quality": encoded(row.quality),
                    },
                )
            ).scalar_one_or_none()
            new_row = int(canonical is not None)
            if canonical is None:
                first = (
                    await db.execute(
                        text(f"""
                    SELECT id,receipt_id,quality::text AS quality_json FROM weather.{row.family}
                    WHERE device_id=:device AND message_type=:family AND source_ts=:source
                    AND row_idx=:idx
                """),
                        {
                            "device": device,
                            "family": row.family,
                            "source": source_at,
                            "idx": row.ordinal,
                        },
                    )
                ).one()
                canonical = first.id
                first_quality = json.loads(
                    first.quality_json, parse_float=Decimal, parse_int=Decimal
                )
                fields = disputed_fields(first_quality, private_storage(row.quality))
                if fields or first_quality.get("extra_fields") != private_storage(row.extra):
                    await self._conflict(
                        db,
                        row,
                        first.id,
                        first.receipt_id,
                        receipt.id,
                        source_at,
                        received_at,
                        fields,
                    )
        await db.execute(
            text("""
            INSERT INTO weather.receipt_row_provenance
              (receipt_id,family,row_idx,parser_revision,observation_id,fingerprint,
               row_evidence,quarantine_reason)
            VALUES(:receipt,:family,:idx,:parser,:canonical,:fingerprint,
                   CAST(:evidence AS jsonb),:reason)
            ON CONFLICT(receipt_id,family,row_idx,parser_revision) DO NOTHING
        """),
            {
                "receipt": receipt.id,
                "family": row.family,
                "idx": row.ordinal,
                "parser": PARSER_REVISION,
                "canonical": canonical,
                "fingerprint": row.fingerprint,
                "evidence": encoded({"raw_row": row.raw_row, "quality": row.quality}),
                "reason": reason,
            },
        )
        if canonical is not None and row.family == "evt_strike":
            await db.execute(
                text("SELECT weather.mark_strike_multiplicity(:id)"), {"id": canonical}
            )
        return new_row

    async def _conflict(
        self,
        db: AsyncConnection,
        row: Row,
        observation: uuid.UUID,
        first_receipt: uuid.UUID,
        second_receipt: uuid.UUID,
        source_at: datetime | None,
        received_at: datetime,
        fields: list[str],
    ) -> None:
        incident = (
            await db.execute(
                text("""
            INSERT INTO weather.observation_conflict
              (family,observation_id,first_receipt_id,conflicting_receipt_id,fields,source_ts,received_at)
            VALUES(:family,:observation,:first,:second,:fields,:source,:received)
            ON CONFLICT(family,observation_id,conflicting_receipt_id) DO NOTHING RETURNING id
        """),
                {
                    "family": row.family,
                    "observation": observation,
                    "first": first_receipt,
                    "second": second_receipt,
                    "fields": fields,
                    "source": source_at,
                    "received": received_at,
                },
            )
        ).scalar_one_or_none()
        if incident is not None:
            await db.execute(
                text("SELECT weather.qualify_telemetry_conflict(:id)"), {"id": incident}
            )
