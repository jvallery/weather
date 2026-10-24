# SPDX-License-Identifier: MIT
"""Current slice-1 metadata: the `alembic check` drift reference and test truncation list.

Migrations never import this module; each revision carries its own inline DDL. Change this
metadata together with a new expand/contract revision.
"""

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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
        # 0002_api: database-assigned insert time; export cursors snapshot on it.
        _observation.append_column(
            Column(
                "inserted_at",
                DateTime(timezone=True),
                nullable=False,
                server_default=text("clock_timestamp()"),
            )
        )
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
Index(
    "ix_outbox_job_id",
    outbox.c.job_id,
    postgresql_where=outbox.c.job_id.is_not(None),
)


# Publication catalog (#18, revision 0003_publication). CHECKs mirror the revision's DDL.
PRODUCT = "product IN ('video','keogram','still')"
CAMERA = "camera_id ~ '^[a-z][a-z0-9_]{0,31}$'"
PRODUCT_KEY = "product_key ~ '^[0-9A-Za-z][0-9A-Za-z:/_.-]{0,63}$'"
WITHDRAWAL = "withdrawn = (withdrawn_at IS NOT NULL) AND withdrawn = (withdraw_reason IS NOT NULL)"


def _withdrawn() -> list[Any]:
    return [
        Column("withdrawn", Boolean, nullable=False, server_default=text("false")),
        _instant("withdrawn_at", True),
        Column("withdraw_reason", String(500)),
    ]


Table(
    "media_asset",
    metadata,
    _identity(),
    Column("class", String(16), nullable=False),
    Column("bucket", String(32), nullable=False),
    Column("visibility", String(8), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("extension", String(8), nullable=False),
    Column("object_key", String(96), nullable=False, unique=True),
    Column("bytes", BigInteger, nullable=False),
    Column("content_type", String(64), nullable=False),
    Column("width", Integer),
    Column("height", Integer),
    _instant("public_not_before", True),
    _instant("published_at", True),
    Column("sidecar", LargeBinary),
    Column("state", String(16), nullable=False, server_default=text("'available'")),
    *_withdrawn(),
    _created(),
    UniqueConstraint("id", "class"),
    CheckConstraint(
        "class IN ('video','poster','keogram','still','sidecar','original')", name="class"
    ),
    CheckConstraint(
        "bucket IN ('weather-media','weather-originals') AND visibility IN ('public','private') "
        "AND (class = 'original') = (bucket = 'weather-originals') "
        "AND (class = 'original') = (visibility = 'private')",
        name="original_private",
    ),
    CheckConstraint(
        "(class = 'original') = (public_not_before IS NULL) "
        "AND (class = 'original') = (published_at IS NULL)",
        name="embargo",
    ),
    CheckConstraint(
        "sha256 ~ '^[0-9a-f]{64}$' AND extension ~ '^[a-z0-9]{1,8}$' AND bytes > 0 "
        "AND object_key = class || '/' || sha256 || '.' || extension",
        name="content_address",
    ),
    CheckConstraint(
        "(width IS NULL) = (height IS NULL) AND (width IS NULL OR (width > 0 AND height > 0))",
        name="geometry",
    ),
    CheckConstraint(
        "(class = 'sidecar') = (sidecar IS NOT NULL) AND (sidecar IS NULL OR "
        "(octet_length(sidecar) = bytes AND bytes <= 1048576 "
        "AND encode(pg_catalog.sha256(sidecar),'hex') = sha256))",
        name="sidecar",
    ),
    CheckConstraint("state IN ('available','unavailable')", name="state"),
    CheckConstraint(WITHDRAWAL, name="withdrawal"),
)
intent = Table(
    "upload_intent",
    metadata,
    _identity(),
    Column("fence", BigInteger, nullable=False, unique=True),
    Column("product", String(16), nullable=False),
    Column("camera_id", String(32), nullable=False),
    Column("product_key", String(64), nullable=False),
    Column("expected_revision", Integer, nullable=False),
    Column("manifest", JSONB, nullable=False),
    Column("state", String(16), nullable=False, server_default=text("'pending'")),
    Column("reason", String(32)),
    _instant("deadline"),
    _created(),
    _instant("resolved_at", True),
    CheckConstraint(
        f"{PRODUCT} AND {CAMERA} AND {PRODUCT_KEY} AND fence > 0 AND expected_revision >= 0",
        name="identity",
    ),
    CheckConstraint(
        "state IN ('pending','committed','orphan') AND (state = 'pending') = "
        "(resolved_at IS NULL) AND (state = 'orphan') = (reason IS NOT NULL)",
        name="state",
    ),
    CheckConstraint(
        "jsonb_typeof(manifest) = 'object' AND octet_length(manifest::text) <= 1048576",
        name="manifest",
    ),
    CheckConstraint("deadline <= created_at + interval '2 days'", name="deadline"),
)
Index(
    "ix_upload_intent_product",
    intent.c.product,
    intent.c.camera_id,
    intent.c.product_key,
    intent.c.fence,
)
Index(
    "ix_upload_intent_created_at",
    intent.c.created_at,
    postgresql_where=intent.c.state == "pending",
)
publication = Table(
    "publication",
    metadata,
    _identity(),
    Column("product", String(16), nullable=False),
    Column("camera_id", String(32), nullable=False),
    Column("product_key", String(64), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("fence", BigInteger, nullable=False),
    _ref("intent_id", "upload_intent"),
    Column("state", String(16), nullable=False),
    Column("reason", String(64)),
    _instant("source_start"),
    _instant("source_end"),
    _instant("public_not_before"),
    Column(
        "committed_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint("product", "camera_id", "product_key", "revision"),
    UniqueConstraint("id", "product"),
    CheckConstraint(
        f"{PRODUCT} AND {CAMERA} AND {PRODUCT_KEY} AND revision > 0 AND fence > 0",
        name="identity",
    ),
    CheckConstraint(
        "state IN ('published','partial','unavailable') "
        "AND (state = 'published' OR reason IS NOT NULL)",
        name="state",
    ),
    CheckConstraint(
        "source_start < source_end AND public_not_before >= source_end + interval '630 seconds'",
        name="embargo",
    ),
)
Index("ix_publication_intent_id", publication.c.intent_id)
publication_asset = Table(
    "publication_asset",
    metadata,
    Column("publication_id", PGUUID(as_uuid=True), primary_key=True),
    Column("product", String(16), nullable=False),
    Column("role", String(16), primary_key=True),
    Column("asset_id", PGUUID(as_uuid=True), nullable=False),
    Column("asset_class", String(16), nullable=False),
    ForeignKeyConstraint(
        ["publication_id", "product"],
        ["weather.publication.id", "weather.publication.product"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["asset_id", "asset_class"],
        ["weather.media_asset.id", "weather.media_asset.class"],
        ondelete="RESTRICT",
    ),
    CheckConstraint("role ~ '^[a-z]+(_[a-z0-9]+)?$'", name="role"),
    CheckConstraint(
        "(asset_class = 'original') = (role = 'original') "
        "AND (asset_class <> 'original' OR product = 'still')",
        name="original_private",
    ),
)
Index("ix_publication_asset_asset_id", publication_asset.c.asset_id)
Table(
    "video",
    metadata,
    _identity(),
    Column("camera_id", String(32), nullable=False),
    Column("cycle_date", Date, nullable=False),
    Column("segment", String(16), nullable=False),
    _ref("publication_id", "publication"),
    Column("revision", Integer, nullable=False),
    Column("fence", BigInteger, nullable=False),
    Column("state", String(16), nullable=False),
    Column("reason", String(64)),
    _instant("starts_at"),
    _instant("ends_at"),
    Column("frames", Integer, nullable=False),
    _instant("public_not_before"),
    _instant("committed_at"),
    *_withdrawn(),
    _created(),
    UniqueConstraint("camera_id", "cycle_date", "segment"),
    CheckConstraint(
        f"{CAMERA} AND segment IN ('daytime','overnight') AND revision > 0 AND fence > 0 "
        "AND frames BETWEEN 0 AND 7200 AND starts_at < ends_at",
        name="identity",
    ),
    CheckConstraint(
        "state IN ('published','partial','unavailable') "
        "AND (state = 'published' OR reason IS NOT NULL)",
        name="state",
    ),
    CheckConstraint("public_not_before >= ends_at + interval '630 seconds'", name="embargo"),
    CheckConstraint(WITHDRAWAL, name="withdrawal"),
)
Table(
    "withdrawal",
    metadata,
    _identity(),
    _ref("video_id", "video", True),
    _ref("asset_id", "media_asset", True),
    Column("reason", String(500), nullable=False),
    Column("actor", String(64), nullable=False),
    _created(),
    CheckConstraint(
        "(video_id IS NULL) <> (asset_id IS NULL) AND length(reason) > 0 AND length(actor) > 0",
        name="target",
    ),
)
Table(
    "product_tombstone",
    metadata,
    Column("product", String(16), primary_key=True),
    Column("camera_id", String(32), primary_key=True),
    Column("product_key", String(64), primary_key=True),
    _ref("withdrawal_id", "withdrawal"),
    _created(),
    CheckConstraint(f"{PRODUCT} AND {CAMERA} AND {PRODUCT_KEY}", name="identity"),
)
