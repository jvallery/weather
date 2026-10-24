# SPDX-License-Identifier: MIT
"""Immutable aggregate revisions and their compare-and-swap current pointers."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

TABLE_NAMES = ("aggregate_revision", "aggregate_head")


def extend_metadata(metadata: MetaData) -> None:
    Table(
        "aggregate_revision",
        metadata,
        Column("id", String(128), primary_key=True),
        Column("scope_key", String(64), nullable=False, index=True),
        Column("station_alias", String(64), nullable=False),
        Column("starts_at", DateTime(timezone=True), nullable=False),
        Column("ends_at", DateTime(timezone=True), nullable=False),
        Column("metric", String(64), nullable=False),
        Column("statistic", String(64), nullable=False),
        Column("policy_revision", String(128), nullable=False),
        Column("watermark", BigInteger, nullable=False),
        Column("evaluated_at", DateTime(timezone=True), nullable=False),
        Column("policy_document", JSONB, nullable=False),
        Column("document", JSONB, nullable=False),
        Column("exact_numerator", Text),
        Column("exact_denominator", Text),
        Column("details", JSONB),
        Column(
            "created_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=text("clock_timestamp()"),
        ),
        CheckConstraint("starts_at < ends_at AND watermark>=0", name="interval_watermark"),
        CheckConstraint(
            "jsonb_typeof(policy_document)='object' AND octet_length(policy_document::text)<=65536",
            name="policy_snapshot",
        ),
        CheckConstraint(
            "id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$' AND scope_key ~ '^[0-9a-f]{64}$'",
            name="identity",
        ),
        CheckConstraint("(exact_numerator IS NULL)=(exact_denominator IS NULL)", name="exact_pair"),
        CheckConstraint(
            "exact_numerator IS NULL OR (length(exact_numerator)<=4096 AND "
            "length(exact_denominator)<=4096 AND "
            "exact_numerator ~ '^-?(0|[1-9][0-9]*)$' AND "
            "exact_denominator ~ '^[1-9][0-9]*$')",
            name="exact_format",
        ),
        CheckConstraint(
            "jsonb_typeof(document)='object' AND "
            "document->>'metric'=metric AND document->>'statistic'=statistic "
            "AND document ?& ARRAY['metric','statistic','value','unit','interval','coverage',"
            "'evidence_ids','evidence_revision','algorithm_revision','reason']::text[] "
            "AND document - ARRAY['metric','statistic','value','unit','interval',"
            "'coverage','evidence_ids','evidence_revision','algorithm_revision','reason']"
            "::text[] = '{}'::jsonb",
            name="public_document",
        ),
        schema="weather",
    )
    Table(
        "aggregate_head",
        metadata,
        Column("scope_key", String(64), primary_key=True),
        Column("revision_id", String(128), ForeignKey("weather.aggregate_revision.id")),
        Column("revision", BigInteger, nullable=False, server_default=text("0")),
        Column("watermark", BigInteger, nullable=False, server_default=text("0")),
        CheckConstraint("revision>=0 AND watermark>=0", name="nonnegative"),
        CheckConstraint(
            "scope_key ~ '^[0-9a-f]{64}$' AND (revision_id IS NULL)=(revision=0)",
            name="head_identity",
        ),
        schema="weather",
    )
