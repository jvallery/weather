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
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, DOUBLE_PRECISION, JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType[Any]):
    """pgvector column type for drift checks only; values are bound as text and cast in SQL."""

    cache_ok = True

    def __init__(self, dimensions: int = 2560) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **kw: Any) -> str:
        return f"VECTOR({self.dimensions})"


ischema_names["vector"] = Vector

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
    # 0009_stills (#38): a still row's recorded hash is the archived original's.
    UniqueConstraint("id", "class", "sha256"),
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
    CheckConstraint("state IN ('available','unavailable','expired')", name="state"),
    CheckConstraint(WITHDRAWAL, name="withdrawal"),
    # 0010_cleanup (#19): when the state last changed, for the GC grace period.
    _instant("state_changed_at", True),
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
    # 0009_stills (#38): a still row's publication is the same camera's still product.
    UniqueConstraint("id", "product", "camera_id"),
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

# 0004_search (#25): embedding profiles, immutable search document revisions and vectors.
Table(
    "embedding_profile",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("model", String(128), nullable=False),
    Column("dimensions", Integer, nullable=False),
    Column("query_prefix", Text, nullable=False),
    _created(),
    CheckConstraint("id ~ '^[a-z0-9][a-z0-9.-]{0,63}$' AND dimensions = 2560", name="identity"),
)
search_document = Table(
    "search_document",
    metadata,
    _identity(),
    _ref("video_id", "video"),
    Column("kind", String(8), nullable=False),
    Column("camera_id", String(32), nullable=False),
    Column("segment", String(16), nullable=False),
    _instant("video_start"),
    _instant("video_end"),
    _instant("source_start"),
    _instant("source_end"),
    Column("media_start_seconds", DOUBLE_PRECISION),
    Column("media_end_seconds", DOUBLE_PRECISION),
    Column("media_revision", Integer, nullable=False),
    Column("description_revision", Integer, nullable=False),
    Column("editorial_revision", Integer, nullable=False),
    Column("analysis_mode", String(32), nullable=False),
    Column("poster_asset_id", PGUUID(as_uuid=True)),
    _instant("public_not_before"),
    Column("text", Text, nullable=False),
    Column("text_hash", String(64), nullable=False),
    # The migration spells the configuration 'pg_catalog.english'; PostgreSQL reflects this.
    Column("lexical", TSVECTOR, Computed("to_tsvector('english'::regconfig, text)")),
    Column("visibility", String(16), nullable=False, server_default=text("'public'")),
    Column("index_status", String(16), nullable=False),
    _instant("withdrawn_at", True),
    _created(),
    UniqueConstraint("id", "text_hash"),
    CheckConstraint("kind IN ('video','window')", name="identity"),
    CheckConstraint("video_start < video_end", name="interval"),
    CheckConstraint("media_start_seconds IS NULL OR media_start_seconds >= 0", name="seek"),
    CheckConstraint("public_not_before >= video_end + interval '630 seconds'", name="embargo"),
    CheckConstraint("length(text) BETWEEN 1 AND 8000", name="text"),
    CheckConstraint("visibility IN ('public','withdrawn')", name="state"),
)
Index(
    "uq_search_document_video",
    search_document.c.video_id,
    unique=True,
    postgresql_where=text("kind = 'video' AND visibility = 'public'"),
)
Index(
    "uq_search_document_window",
    search_document.c.video_id,
    search_document.c.source_start,
    unique=True,
    postgresql_where=text("kind = 'window' AND visibility = 'public'"),
)
Index("ix_search_document_video_id", search_document.c.video_id)
Index(
    "ix_search_document_lexical",
    search_document.c.lexical,
    postgresql_using="gin",
    postgresql_where=text("visibility = 'public'"),
)
Table(
    "search_embedding",
    metadata,
    Column("document_id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "profile_id",
        String(64),
        ForeignKey("weather.embedding_profile.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("text_hash", String(64), nullable=False),
    Column("embedding", Vector(2560), nullable=False),
    _created(),
    ForeignKeyConstraint(
        ["document_id", "text_hash"],
        ["weather.search_document.id", "weather.search_document.text_hash"],
        ondelete="RESTRICT",
    ),
    CheckConstraint("abs(public.vector_norm(embedding) - 1) <= 0.001", name="normalized"),
)
# 0005_events (#41): identity, immutable revisions (stamped by a trigger) and day links.
weather_event = Table(
    "weather_event",
    metadata,
    _identity(),
    Column("logical_key", String(256), nullable=False, unique=True),
    Column("rule_family", String(32), nullable=False),
    Column("revision", Integer, nullable=False, server_default=text("0")),
    _created(),
    CheckConstraint(
        "rule_family IN ('precipitation_onset','daily_gust_max','lightning_strike',"
        "'lightning_interval','visual','synthesis','manual')",
        name="rule_family",
    ),
    CheckConstraint("revision >= 0", name="revision"),
)
event_revision = Table(
    "weather_event_revision",
    metadata,
    Column(
        "event_id",
        PGUUID(as_uuid=True),
        ForeignKey("weather.weather_event.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("revision", Integer, primary_key=True, autoincrement=False),
    Column("recorded_xid", BigInteger, nullable=False),
    _instant("recorded_at"),
    Column("visibility", String(16), nullable=False),
    Column("withdrawal_reason", String(32)),
    Column("origin", String(16), nullable=False),
    Column("basis", String(16), nullable=False),
    Column("camera_id", String(32)),
    _instant("starts_at"),
    _instant("ends_at"),
    Column("time_precision", String(16), nullable=False),
    Column("rule_revision", String(64), nullable=False),
    Column("author_kind", String(16), nullable=False),
    Column("author", String(256)),
    Column("reason", Text),
    Column("label", String(240)),
    _data("measurement"),
    Column(
        "evidence_ids",
        ARRAY(PGUUID(as_uuid=True)),
        nullable=False,
        server_default=text("'{}'::uuid[]"),
    ),
    Column("evidence_hash", String(64), nullable=False),
    CheckConstraint("revision > 0", name="number"),
    CheckConstraint("starts_at < ends_at", name="interval"),
)
Index("ix_weather_event_revision_starts_at", event_revision.c.starts_at, event_revision.c.event_id)
event_day = Table(
    "weather_event_day",
    metadata,
    Column("event_id", PGUUID(as_uuid=True), primary_key=True),
    Column("revision", Integer, primary_key=True, autoincrement=False),
    Column(
        "local_date",
        Date,
        ForeignKey("weather.weather_day.local_date", ondelete="RESTRICT"),
        primary_key=True,
    ),
    ForeignKeyConstraint(
        ["event_id", "revision"],
        ["weather.weather_event_revision.event_id", "weather.weather_event_revision.revision"],
        ondelete="RESTRICT",
    ),
)
Index("ix_weather_event_day_local_date", event_day.c.local_date)

# 0006_render (#15): frozen solar schedule and per-camera render segments. The overlap
# exclusion on current segments (btree_gist) lives in the migration only; alembic ignores the
# index that backs it.
CAMERA_IDS = "'skycam_east','skycam_west','frontyard_1'"
Table(
    "solar_cycle",
    metadata,
    Column("site", String(32), primary_key=True),
    Column(
        "cycle_date",
        Date,
        ForeignKey("weather.weather_day.local_date", ondelete="RESTRICT"),
        primary_key=True,
    ),
    _instant("dawn"),
    _instant("dusk"),
    _instant("next_dawn"),
    Column("day_frames", Integer, nullable=False),
    Column("night_frames", Integer, nullable=False),
    Column("provenance_hash", String(64), nullable=False),
    Column("astral_version", String(16), nullable=False),
    Column("tzdata_version", String(16), nullable=False),
    _created(),
    CheckConstraint(
        "site ~ '^[a-z][a-z0-9_]{0,31}$' AND dawn < dusk AND dusk < next_dawn", name="order"
    ),
    CheckConstraint(
        "day_frames > 0 AND night_frames > 0 AND day_frames + night_frames = 7200",
        name="allocation",
    ),
    CheckConstraint("provenance_hash ~ '^[0-9a-f]{64}$'", name="provenance"),
)
segment = Table(
    "segment",
    metadata,
    _identity(),
    Column("site", String(32), nullable=False),
    Column("cycle_date", Date, nullable=False),
    Column("camera_id", String(32), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("revision", Integer, nullable=False, server_default=text("1")),
    Column("current", Boolean, nullable=False, server_default=text("true")),
    _instant("starts_at"),
    _instant("ends_at"),
    Column("frames", Integer, nullable=False),
    Column("state", String(16), nullable=False, server_default=text("'scheduled'")),
    Column("reason", String(64)),
    Column("slot_coverage", DOUBLE_PRECISION),
    _instant("mezzanine_through", True),
    # 0010_cleanup (#19): the recipe whose durable blocks mezzanine_through describes.
    Column("mezzanine_recipe", String(80)),
    _instant("mezzanine_durable_at", True),
    Column("render_result", JSONB),
    _instant("finalized_at", True),
    _created(),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint("camera_id", "cycle_date", "kind", "revision"),
    ForeignKeyConstraint(
        ["site", "cycle_date"],
        ["weather.solar_cycle.site", "weather.solar_cycle.cycle_date"],
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        f"camera_id IN ({CAMERA_IDS}) AND kind IN ('daytime','overnight') AND revision > 0",
        name="identity",
    ),
    CheckConstraint("starts_at < ends_at AND frames BETWEEN 1 AND 7200", name="interval"),
    CheckConstraint(
        "state IN ('scheduled','processing','published','partial','unavailable') "
        "AND (state IN ('partial','unavailable')) = (reason IS NOT NULL)",
        name="state",
    ),
    CheckConstraint(
        "slot_coverage IS NULL OR (slot_coverage >= 0 AND slot_coverage <= 1)", name="coverage"
    ),
    CheckConstraint(
        "render_result IS NULL OR (jsonb_typeof(render_result) = 'object' "
        "AND octet_length(render_result::text) <= 1048576)",
        name="result",
    ),
)
Index(
    "uq_segment_current",
    segment.c.camera_id,
    segment.c.cycle_date,
    segment.c.kind,
    unique=True,
    postgresql_where=segment.c.current,
)
Index("ix_segment_ends_at", segment.c.ends_at, postgresql_where=segment.c.current)

# 0009_stills (#38): hourly slots, archived originals or explicit missing slots, and their
# sanitized public derivatives. The CHECKs and the slot-in-day trigger live in the migration.
hour_slot = Table(
    "hour_slot",
    metadata,
    Column("starts_at", DateTime(timezone=True), primary_key=True),
    Column(
        "local_date",
        Date,
        ForeignKey("weather.weather_day.local_date", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("utc_offset_minutes", SmallInteger, nullable=False),
    Column("fold", SmallInteger, nullable=False),
    UniqueConstraint("starts_at", "local_date"),
)
archival_still = Table(
    "archival_still",
    metadata,
    _identity(),
    Column("camera_id", String(32), nullable=False),
    _instant("slot_starts_at"),
    Column("local_date", Date, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("state", String(16), nullable=False),
    Column("reason", String(64)),
    Column("policy", String(64), nullable=False),
    Column("eligible", Integer, nullable=False),
    _data("rejections"),
    Column("attempt_id", String(128)),
    Column("chunk", String(256)),
    Column("member", String(128)),
    _instant("source_ts", True),
    _instant("capture_upper", True),
    Column("offset_us", BigInteger),
    Column("cycle_date", Date),
    Column("segment_kind", String(16)),
    Column("segment_revision", Integer),
    Column("original_asset_id", PGUUID(as_uuid=True)),
    Column("original_class", String(16)),
    Column("sha256", String(64)),
    Column("bytes", BigInteger),
    Column("width", Integer),
    Column("height", Integer),
    Column("publication_id", PGUUID(as_uuid=True)),
    Column("publication_product", String(16)),
    Column("retain_until", Date, nullable=False),
    _created(),
    _instant("retired_at", True),
    UniqueConstraint("camera_id", "slot_starts_at", "revision"),
    ForeignKeyConstraint(
        ["slot_starts_at", "local_date"],
        ["weather.hour_slot.starts_at", "weather.hour_slot.local_date"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["camera_id", "cycle_date", "segment_kind", "segment_revision"],
        [
            "weather.segment.camera_id",
            "weather.segment.cycle_date",
            "weather.segment.kind",
            "weather.segment.revision",
        ],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["original_asset_id", "original_class", "sha256"],
        ["weather.media_asset.id", "weather.media_asset.class", "weather.media_asset.sha256"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["publication_id", "publication_product", "camera_id"],
        [
            "weather.publication.id",
            "weather.publication.product",
            "weather.publication.camera_id",
        ],
        ondelete="RESTRICT",
    ),
)
Index(
    "uq_archival_still_active",
    archival_still.c.camera_id,
    archival_still.c.slot_starts_at,
    unique=True,
    postgresql_where=archival_still.c.active,
)
Index(
    "ix_archival_still_local_date",
    archival_still.c.local_date,
    postgresql_where=archival_still.c.active,
)
still_derivative = Table(
    "still_derivative",
    metadata,
    Column("archival_still_id", PGUUID(as_uuid=True), primary_key=True),
    Column("role", String(16), primary_key=True),
    Column("asset_id", PGUUID(as_uuid=True), nullable=False),
    Column("asset_class", String(16), nullable=False, server_default=text("'still'")),
    Column("recipe", String(64), nullable=False),
    Column("state", String(16), nullable=False, server_default=text("'published'")),
    _created(),
    ForeignKeyConstraint(["archival_still_id"], ["weather.archival_still.id"], ondelete="RESTRICT"),
    ForeignKeyConstraint(
        ["asset_id", "asset_class"],
        ["weather.media_asset.id", "weather.media_asset.class"],
        ondelete="RESTRICT",
    ),
)
Index("ix_still_derivative_asset_id", still_derivative.c.asset_id)
# 0007_previews (#39): delayed camera preview pointers, one row per generation and variant.
camera_preview = Table(
    "camera_preview",
    metadata,
    _identity(),
    Column("camera_id", String(32), nullable=False),
    Column("generation", BigInteger, nullable=False),
    Column("variant", String(8), nullable=False),
    Column("width", Integer, nullable=False),
    Column("height", Integer, nullable=False),
    Column("bytes", Integer, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("source_sha256", String(64), nullable=False),
    Column("path", String(96), nullable=False),
    _instant("captured_lower"),
    _instant("captured_upper"),
    _instant("public_not_before"),
    _instant("first_published_at"),
    _instant("expires_at"),
    _instant("withdrawn_at", True),
    _created(),
    UniqueConstraint("camera_id", "generation", "variant"),
    CheckConstraint(
        f"camera_id IN ({CAMERA_IDS}) AND generation > 0 AND variant IN ('large','small')",
        name="identity",
    ),
    CheckConstraint(
        "width BETWEEN 1 AND 1280 AND height BETWEEN 1 AND 16384 "
        "AND bytes BETWEEN 1 AND 2097152 AND sha256 ~ '^[0-9a-f]{64}$' "
        "AND source_sha256 ~ '^[0-9a-f]{64}$'",
        name="image",
    ),
    CheckConstraint(
        "path = 'previews/' || camera_id || '/' || generation || '-' || variant || '.jpg'",
        name="path",
    ),
    CheckConstraint("captured_lower <= captured_upper", name="capture"),
    CheckConstraint(
        "public_not_before >= captured_upper + interval '630 seconds'", name="embargo"
    ),
    CheckConstraint(
        "first_published_at >= public_not_before AND first_published_at < expires_at",
        name="published",
    ),
    CheckConstraint("expires_at = captured_lower + interval '3600 seconds'", name="expiry"),
    CheckConstraint("to_timestamp(generation * 60) <= first_published_at", name="generation"),
)
# 0010_cleanup (#19): staging chunk inventory, staging deletion audit, object lifecycle audit.
staging_chunk = Table(
    "staging_chunk",
    metadata,
    _identity(),
    Column("camera_id", String(32), nullable=False),
    Column("name", String(192), nullable=False, unique=True),
    _instant("window_start"),
    _instant("window_end"),
    _instant("newest_capture_at"),
    Column("frames", Integer, nullable=False),
    Column("bytes", BigInteger, nullable=False),
    Column("manifest_sha256", String(64), nullable=False),
    Column("cadence_ms", Integer, nullable=False),
    Column("phase_ms", Integer, nullable=False),
    Column("width", Integer, nullable=False),
    Column("height", Integer, nullable=False),
    Column("state", String(16), nullable=False, server_default=text("'present'")),
    Column(
        "discovered_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    _instant("resolved_at", True),
    CheckConstraint(
        f"camera_id IN ({CAMERA_IDS}) AND window_start < window_end "
        "AND newest_capture_at >= window_end AND frames >= 0 AND bytes > 0 "
        "AND manifest_sha256 ~ '^[0-9a-f]{64}$'",
        name="identity",
    ),
    CheckConstraint(
        "state IN ('present','deleted','missing') AND (state = 'present') = (resolved_at IS NULL)",
        name="state",
    ),
)
Index(
    "ix_staging_chunk_camera_id",
    staging_chunk.c.camera_id,
    staging_chunk.c.window_start,
    postgresql_where=text("state = 'present'"),
)
staging_deletion = Table(
    "staging_deletion",
    metadata,
    _identity(),
    Column("class", String(16), nullable=False),
    Column("camera_id", String(32), nullable=False),
    Column("name", String(192), nullable=False, unique=True),
    _ref("chunk_id", "staging_chunk", True),
    _ref("segment_id", "segment", True),
    Column("sha256", String(64)),
    Column("bytes", BigInteger, nullable=False),
    Column("reason", String(64), nullable=False),
    Column(
        "decided_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    _instant("unlinked_at", True),
    CheckConstraint(
        "class IN ('chunk','tmp','render') AND (class = 'chunk') = (chunk_id IS NOT NULL) "
        "AND (class = 'render') = (segment_id IS NOT NULL)",
        name="target",
    ),
)
Index(
    "ix_staging_deletion_camera_id",
    staging_deletion.c.camera_id,
    postgresql_where=text("unlinked_at IS NULL"),
)
object_event = Table(
    "object_event",
    metadata,
    _identity(),
    Column("action", String(16), nullable=False),
    Column("bucket", String(32), nullable=False),
    Column("object_key", String(96), nullable=False),
    _ref("asset_id", "media_asset", True),
    Column("reason", String(64), nullable=False),
    Column("actor", String(64), nullable=False),
    _created(),
    CheckConstraint(
        "action IN ('unavailable','expired','gc_deleted','purged')", name="action"
    ),
)
Index("ix_object_event_object_key", object_event.c.object_key)

# 0011_analysis (#23): analysis heads, immutable revisions and 24 h private raw responses.
analysis = Table(
    "analysis",
    metadata,
    Column(
        "video_id",
        PGUUID(as_uuid=True),
        ForeignKey("weather.video.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    _ref("publication_id", "publication"),
    Column("media_sha256", String(64), nullable=False),
    Column("state", String(16), nullable=False),
    Column("reason", String(32)),
    Column("revision", Integer, nullable=False, server_default=text("0")),
    Column("stale", Boolean, nullable=False, server_default=text("false")),
    Column("runs", Integer, nullable=False, server_default=text("0")),
    Column("calls", Integer, nullable=False, server_default=text("0")),
    Column("refreshes", Integer, nullable=False, server_default=text("0")),
    Column("attempt_calls", Integer, nullable=False, server_default=text("0")),
    _instant("pending_since", True),
    _instant("attempted_at", True),
    _instant("next_attempt_at", True),
    _instant("evidence_checked_at", True),
    _created(),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    CheckConstraint("state IN ('pending','complete','degraded','failed')", name="state"),
)
Index("ix_analysis_state", analysis.c.state, analysis.c.next_attempt_at)
Table(
    "analysis_revision",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    _ref("video_id", "video"),
    Column("revision", Integer, nullable=False),
    _ref("publication_id", "publication"),
    Column("media_sha256", String(64), nullable=False),
    Column("state", String(16), nullable=False),
    Column("reason", String(32)),
    Column("refresh", String(16), nullable=False),
    Column("visual_id", PGUUID(as_uuid=True), nullable=False),
    Column("model_route", String(64), nullable=False),
    Column("model", String(128), nullable=False),
    Column("prompt_sha256", String(64), nullable=False),
    Column("schema_sha256", String(64), nullable=False),
    Column("evidence_hash", String(64), nullable=False),
    Column("evidence_count", Integer, nullable=False),
    Column("summary", Text),
    Column("claims", JSONB, nullable=False),
    Column("observations", JSONB, nullable=False),
    Column("limitations", JSONB, nullable=False),
    Column("coverage", JSONB, nullable=False),
    Column("inputs", JSONB, nullable=False),
    Column("event_keys", JSONB, nullable=False),
    Column("calls", Integer, nullable=False),
    Column("author_kind", String(16), nullable=False, server_default=text("'model'")),
    _created(),
    UniqueConstraint("video_id", "revision"),
    CheckConstraint("state IN ('complete','degraded')", name="state"),
)
Table(
    "ai_raw_response",
    metadata,
    _identity(),
    _ref("video_id", "video"),
    Column("stage", String(16), nullable=False),
    _instant("window_start", True),
    Column("call", Integer, nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("finish_reason", String(32)),
    Column("body", Text),
    _created(),
    Column(
        "expires_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP + interval '24 hours'"),
    ),
    CheckConstraint(
        "expires_at > created_at AND expires_at <= created_at + interval '24 hours'",
        name="retention",
    ),
)
Index("ix_ai_raw_response_expires_at", metadata.tables["weather.ai_raw_response"].c.expires_at)
