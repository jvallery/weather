# SPDX-License-Identifier: MIT
"""Additive telemetry custody/incident schema; the v1 snapshot stays immutable."""

from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

TABLE_NAMES = (
    "receipt_transport",
    "receipt_row_provenance",
    "receipt_integrity_incident",
    "observation_conflict",
    "telemetry_coverage_interval",
    "telemetry_loss_snapshot",
)


def extend_metadata(metadata: MetaData) -> None:
    def identity() -> Column[Any]:
        return Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
        )

    def receipt(name: str = "receipt_id") -> Column[Any]:
        return Column(
            name, UUID(as_uuid=True), ForeignKey("weather.telemetry_receipt.id"), nullable=False
        )

    def instant(name: str) -> Column[Any]:
        return Column(name, DateTime(timezone=True), nullable=False)

    Table(
        "receipt_transport",
        metadata,
        Column(
            "receipt_id",
            UUID(as_uuid=True),
            ForeignKey("weather.telemetry_receipt.id"),
            primary_key=True,
        ),
        Column("metadata", JSONB, nullable=False),
        Column("metadata_sha256", String(64), nullable=False),
        Column("family", String(32), nullable=False),
        Column("cadence", JSONB),
        Column("device_id", UUID(as_uuid=True), ForeignKey("weather.device.id")),
        schema="weather",
    )
    Table(
        "receipt_row_provenance",
        metadata,
        identity(),
        receipt(),
        Column("family", String(32), nullable=False),
        Column("row_idx", BigInteger, nullable=False),
        Column("parser_revision", String(64), nullable=False),
        Column("observation_id", UUID(as_uuid=True)),
        Column("fingerprint", String(64)),
        Column("row_evidence", JSONB, nullable=False),
        Column("quarantine_reason", String(64)),
        UniqueConstraint("receipt_id", "family", "row_idx", "parser_revision"),
        CheckConstraint("row_idx >= 0", name="row_index"),
        schema="weather",
    )
    Table(
        "receipt_integrity_incident",
        metadata,
        identity(),
        receipt(),
        Column("alternate_raw", LargeBinary, nullable=False),
        Column("alternate_sha256", String(64), nullable=False),
        Column("alternate_metadata", JSONB, nullable=False),
        Column("alternate_metadata_sha256", String(64), nullable=False),
        instant("received_at"),
        UniqueConstraint("receipt_id", "alternate_sha256", "alternate_metadata_sha256"),
        CheckConstraint(
            "alternate_sha256=encode(pg_catalog.sha256(alternate_raw),'hex')", name="digest"
        ),
        schema="weather",
    )
    Table(
        "observation_conflict",
        metadata,
        identity(),
        Column("family", String(32), nullable=False),
        Column("observation_id", UUID(as_uuid=True), nullable=False),
        receipt("first_receipt_id"),
        receipt("conflicting_receipt_id"),
        Column("fields", ARRAY(Text), nullable=False),
        instant("source_ts"),
        instant("received_at"),
        UniqueConstraint("family", "observation_id", "conflicting_receipt_id"),
        schema="weather",
    )
    Table(
        "telemetry_coverage_interval",
        metadata,
        identity(),
        receipt(),
        Column("device_id", UUID(as_uuid=True), ForeignKey("weather.device.id")),
        Column("family", String(32), nullable=False),
        Column("starts_ns", BigInteger, nullable=False),
        Column("ends_ns", BigInteger, nullable=False),
        Column("reason", String(32), nullable=False),
        Column("policy", String(64), nullable=False),
        Column("lost_count", BigInteger),
        UniqueConstraint("receipt_id", "family", "starts_ns", "ends_ns", "reason"),
        CheckConstraint("ends_ns >= starts_ns", name="bounds"),
        CheckConstraint(
            "reason IN ('arrival_gap','capacity_loss','parse_failure',"
            "'reduced_cadence','db_backlog','station_unavailable',"
            "'restart_volatile_unknown','spool_unreadable','receiver_clock_discontinuity')",
            name="reason",
        ),
        schema="weather",
    )

    Table(
        "telemetry_loss_snapshot",
        metadata,
        identity(),
        Column("generation", UUID(as_uuid=True), nullable=False),
        Column("reason", String(32), nullable=False),
        Column("first_ns", BigInteger, nullable=False),
        Column("last_ns", BigInteger, nullable=False),
        Column("lost_count", BigInteger),
        Column("snapshot_sha256", String(64), nullable=False, unique=True),
        CheckConstraint("last_ns >= first_ns", name="bounds"),
        CheckConstraint("lost_count IS NULL OR lost_count>=0", name="count"),
        schema="weather",
    )
