# SPDX-License-Identifier: MIT
"""Immutable migration-0004 snapshot of public qualification revision history."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

TABLE_NAMES = ("telemetry_evidence_revision",)


def extend_metadata(metadata: MetaData) -> None:
    revision = Table(
        "telemetry_evidence_revision",
        metadata,
        Column("revision_seq", BigInteger, primary_key=True, autoincrement=False),
        Column(
            "publication_seq",
            BigInteger,
            ForeignKey("weather.telemetry_publication.publication_seq", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column("id", UUID(as_uuid=True), nullable=False),
        Column("station_alias", String(64), nullable=False),
        Column("device_alias", String(64), nullable=False),
        Column("message_type", String(32), nullable=False),
        Column("observed_at", DateTime(timezone=True), nullable=False),
        Column("received_at", DateTime(timezone=True), nullable=False),
        Column("row_idx", Integer, nullable=False),
        Column("values", JSONB, nullable=False),
        Column("field_quality", JSONB, nullable=False),
        Column("quality", JSONB, nullable=False),
        CheckConstraint("publication_seq > 0 AND revision_seq >= publication_seq", name="order"),
        CheckConstraint("row_idx >= 0", name="row_index"),
        CheckConstraint(
            "message_type IN ('obs_st','rapid_wind','evt_precip','evt_strike','obs_air','obs_sky')",
            name="family",
        ),
        CheckConstraint(
            "jsonb_typeof(\"values\")='object' AND jsonb_typeof(field_quality)='object' "
            "AND jsonb_typeof(quality)='object'",
            name="objects",
        ),
        schema="weather",
    )
    Index(
        "ix_telemetry_evidence_revision_identity_revision",
        revision.c.id,
        revision.c.message_type,
        revision.c.revision_seq.desc(),
    )
    Index(
        "ix_telemetry_evidence_revision_station_time",
        revision.c.station_alias,
        revision.c.observed_at,
    )
