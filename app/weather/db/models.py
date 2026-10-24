# SPDX-License-Identifier: MIT
"""Current slice-1 metadata: the `alembic check` drift reference and test truncation list.

Migrations never import this module; each revision carries its own inline DDL. Change this
metadata together with a new expand/contract revision.
"""

from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

TELEMETRY_FAMILIES = (
    "obs_st",
    "rapid_wind",
    "evt_precip",
    "evt_strike",
    "device_status",
    "hub_status",
    "obs_air",
    "obs_sky",
)
# Current conditions read the newest row of these families, so they get an ordered btree.
CURRENT_FAMILIES = frozenset({"obs_st", "rapid_wind"})

metadata = MetaData(
    schema="weather",
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    },
)


def _identity() -> Column[Any]:
    return Column(
        "id", PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _ref(name: str, target: str, nullable: bool = False) -> Column[Any]:
    return Column(
        name,
        PGUUID(as_uuid=True),
        ForeignKey(f"weather.{target}.id", ondelete="RESTRICT"),
        nullable=nullable,
    )


def _instant(name: str, nullable: bool = False) -> Column[Any]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


def _created() -> Column[Any]:
    return Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


def _data(name: str = "payload") -> Column[Any]:
    return Column(name, JSONB, nullable=False, server_default=text("'{}'::jsonb"))


Table(
    "station",
    metadata,
    _identity(),
    Column("alias", String(64), nullable=False, unique=True),
    Column("site", String(64), nullable=False, unique=True),
    _data("private_configuration"),
    _created(),
)
Table(
    "device",
    metadata,
    _identity(),
    _ref("station_id", "station"),
    Column("alias", String(64), nullable=False, unique=True),
    Column("private_id", Text, nullable=False, unique=True),
    Column("kind", String(32), nullable=False),
    _instant("valid_from"),
    _instant("valid_until", True),
    _data("private_configuration"),
    _created(),
    CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="validity"),
)
receipt = Table(
    "telemetry_receipt",
    metadata,
    _identity(),
    _instant("received_at"),
    Column("raw_bytes", LargeBinary, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("parse_state", String(32), nullable=False),
    Column("parse_reason", String(64)),
    Column("parser_revision", String(64), nullable=False),
    Column("receiver_incarnation", String(128), nullable=False),
    CheckConstraint(
        "sha256 ~ '^[0-9a-f]{64}$' AND sha256=encode(pg_catalog.sha256(raw_bytes),'hex')",
        name="digest",
    ),
    CheckConstraint("parse_state IN ('parsed','unsupported','quarantined')", name="parse_state"),
)
# Btree, not BRIN: reproject pages by the (received_at, id) keyset.
Index("ix_telemetry_receipt_received_id", receipt.c.received_at, receipt.c.id)
for _family in TELEMETRY_FAMILIES:
    _observation = Table(
        _family,
        metadata,
        _identity(),
        _ref("device_id", "device"),
        _ref("receipt_id", "telemetry_receipt"),
        Column("message_type", String(32), nullable=False, server_default=text(f"'{_family}'")),
        _instant("source_ts"),
        Column("row_idx", Integer, nullable=False),
        _data("values"),
        Column("quality", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
        UniqueConstraint("device_id", "message_type", "source_ts", "row_idx"),
        CheckConstraint(f"message_type = '{_family}'", name="family"),
        CheckConstraint("row_idx >= 0", name="row_index"),
        CheckConstraint("jsonb_typeof(\"values\") = 'object'", name="values_object"),
    )
    if _family in CURRENT_FAMILIES:
        Index(f"ix_{_family}_source_ts", _observation.c.source_ts)
    else:
        Index(f"ix_{_family}_source_brin", _observation.c.source_ts, postgresql_using="brin")

Table(
    "weather_day",
    metadata,
    Column("local_date", Date, primary_key=True),
    _instant("starts_at"),
    _instant("ends_at"),
    CheckConstraint(
        "ends_at - starts_at IN (interval '23 hours',interval '24 hours',interval '25 hours')",
        name="interval",
    ),
)

job = Table(
    "job",
    metadata,
    _identity(),
    Column("kind", String(128), nullable=False),
    Column("idempotency_key", String(256), nullable=False, unique=True),
    _data(),
    Column("status", String(16), nullable=False, server_default=text("'queued'")),
    # The attempt number is also the fencing token: each claim increments it.
    Column("attempt", Integer, nullable=False, server_default=text("0")),
    _instant("not_before"),
    _instant("deadline"),
    _instant("lease_until", True),
    Column("last_error", String(128)),
    Column("result", JSONB),
    _created(),
    CheckConstraint("attempt >= 0", name="attempt"),
    CheckConstraint(
        "status IN ('queued','running','succeeded','failed','cancelled','expired')",
        name="status",
    ),
    CheckConstraint(
        "(status='running') = (lease_until IS NOT NULL) AND "
        "(lease_until IS NULL OR lease_until <= deadline)",
        name="lease",
    ),
    CheckConstraint(
        "jsonb_typeof(payload)='object' AND octet_length(payload::text) <= 1048576",
        name="payload",
    ),
)
Index(
    "ix_job_claim",
    job.c.not_before,
    job.c.created_at,
    postgresql_where=job.c.status.in_(["queued", "running"]),
)
outbox = Table(
    "outbox",
    metadata,
    _identity(),
    Column("topic", String(128), nullable=False),
    Column("idempotency_key", String(256), nullable=False, unique=True),
    _data(),
    _instant("not_before"),
    _instant("deadline"),
    _instant("dispatched_at", True),
    _ref("job_id", "job", True),
    # Set with dispatched_at and no job when a row cannot be routed (quarantine).
    Column("error", String(64)),
    _created(),
    CheckConstraint(
        "error IS NULL OR (dispatched_at IS NOT NULL AND job_id IS NULL)", name="quarantine"
    ),
    CheckConstraint(
        "jsonb_typeof(payload)='object' AND octet_length(payload::text) <= 1048576",
        name="payload",
    ),
)
Index(
    "ix_outbox_pending",
    outbox.c.created_at,
    outbox.c.id,
    postgresql_where=outbox.c.dispatched_at.is_(None),
)
