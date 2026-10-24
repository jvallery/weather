# SPDX-License-Identifier: MIT
"""Immutable v1 metadata factory used by the first migration.

Later migrations extend models.metadata; do not change this migration snapshot.
"""

import math
from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
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
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.engine import Dialect
from sqlalchemy.sql.schema import SchemaItem
from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType[list[float]]):
    cache_ok = True

    def __init__(self, dimensions: int = 2560) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **kw: Any) -> str:
        return f"VECTOR({self.dimensions})"

    def bind_processor(self, dialect: Dialect) -> Callable[[Any], str | None]:
        def encode(value: Any) -> str | None:
            if value is None:
                return None
            numbers = [float(number) for number in value]
            if len(numbers) != self.dimensions or not all(
                math.isfinite(n) for n in numbers
            ):
                raise ValueError("Invalid embedding dimensions or finite values")
            return "[" + ",".join(repr(number) for number in numbers) + "]"

        return encode

    def result_processor(
        self, dialect: Dialect, coltype: Any
    ) -> Callable[[Any], list[float] | None]:
        def decode(value: Any) -> list[float] | None:
            if value is None:
                return None
            if isinstance(value, str):
                return [float(number) for number in value[1:-1].split(",")]
            return [float(number) for number in value]

        return decode


ischema_names["vector"] = Vector


def build_metadata() -> MetaData:
    metadata = MetaData(
        schema="weather",
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        },
    )

    def table(name: str, *items: SchemaItem) -> Table:
        return Table(name, metadata, *items)

    def identity(name: str = "id") -> Column[UUID]:
        return Column(
            name,
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=text("gen_random_uuid()"),
        )

    def ref(name: str, target: str, nullable: bool = False) -> Column[UUID]:
        return Column(
            name,
            PGUUID(as_uuid=True),
            ForeignKey(f"weather.{target}.id", ondelete="RESTRICT"),
            nullable=nullable,
        )

    def instant(name: str, nullable: bool = False) -> Column[Any]:
        return Column(name, DateTime(timezone=True), nullable=nullable)

    def created() -> Column[Any]:
        return Column(
            "created_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
        )

    def data(name: str = "payload") -> Column[Any]:
        return Column(name, JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    def digest(name: str = "sha256") -> Column[Any]:
        return Column(name, String(64), nullable=False)

    def withdrawn() -> list[SchemaItem]:
        return [
            Column("withdrawn", Boolean, nullable=False, server_default=text("false")),
            instant("withdrawn_at", True),
            Column("withdraw_reason", Text),
            CheckConstraint(
                "NOT withdrawn OR (withdrawn_at IS NOT NULL AND "
                "withdraw_reason IS NOT NULL)",
                name="withdrawal_reason",
            ),
        ]

    table(
        "station",
        identity(),
        Column("alias", String(64), nullable=False, unique=True),
        Column("site", String(64), nullable=False, unique=True),
        data("private_configuration"),
        created(),
    )
    table(
        "device",
        identity(),
        ref("station_id", "station"),
        Column("alias", String(64), nullable=False, unique=True),
        Column("private_id", Text, nullable=False, unique=True),
        Column("kind", String(32), nullable=False),
        instant("valid_from"),
        instant("valid_until", True),
        data("private_configuration"),
        created(),
        CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from", name="validity"
        ),
    )
    receipt = table(
        "telemetry_receipt",
        identity(),
        instant("received_at"),
        Column("raw_bytes", LargeBinary, nullable=False),
        Column("parsed_json", JSONB),
        digest(),
        Column("parse_state", String(32), nullable=False),
        Column("parser_revision", String(64), nullable=False),
        Column("receiver_incarnation", String(128), nullable=False),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$' AND "
            "sha256=encode(pg_catalog.sha256(raw_bytes),'hex')",
            name="digest",
        ),
        CheckConstraint(
            "parse_state IN ('received','parsed','malformed',"
            "'unsupported','quarantined')",
            name="parse_state",
        ),
    )
    Index(
        "ix_telemetry_receipt_received_brin",
        receipt.c.received_at,
        postgresql_using="brin",
    )
    for family in (
        "obs_st",
        "rapid_wind",
        "evt_precip",
        "evt_strike",
        "device_status",
        "hub_status",
        "obs_air",
        "obs_sky",
    ):
        observation = table(
            family,
            identity(),
            ref("device_id", "device"),
            ref("receipt_id", "telemetry_receipt"),
            Column(
                "message_type",
                String(32),
                nullable=False,
                server_default=text(f"'{family}'"),
            ),
            instant("source_ts"),
            Column("row_idx", Integer, nullable=False),
            data("values"),
            Column(
                "quality", JSONB, nullable=False, server_default=text("'{}'::jsonb")
            ),
            UniqueConstraint("device_id", "message_type", "source_ts", "row_idx"),
            CheckConstraint(f"message_type = '{family}'", name="family"),
            CheckConstraint("row_idx >= 0", name="row_index"),
            CheckConstraint(
                "jsonb_typeof(\"values\") = 'object'", name="values_object"
            ),
        )
        Index(
            f"ix_{family}_source_brin", observation.c.source_ts, postgresql_using="brin"
        )

    table(
        "weather_day",
        identity(),
        Column("site", String(64), nullable=False),
        Column("local_date", Date, nullable=False),
        Column(
            "timezone",
            String(64),
            nullable=False,
            server_default=text("'America/Denver'"),
        ),
        instant("starts_at"),
        instant("ends_at"),
        UniqueConstraint("site", "local_date"),
        CheckConstraint("ends_at > starts_at", name="interval"),
    )
    table(
        "archive_hour_slot",
        identity(),
        ref("weather_day_id", "weather_day"),
        Column("site", String(64), nullable=False),
        instant("starts_at"),
        instant("ends_at"),
        Column("local_label", String(5), nullable=False),
        Column("utc_offset_minutes", Integer, nullable=False),
        Column("fold", Integer, nullable=False),
        UniqueConstraint("site", "starts_at"),
        UniqueConstraint("weather_day_id", "id"),
        CheckConstraint("ends_at = starts_at + interval '1 hour'", name="one_hour"),
        CheckConstraint("fold IN (0,1)", name="fold"),
    )
    table(
        "camera",
        Column("id", String(32), primary_key=True),
        Column("label", String(64), nullable=False),
        Column("enabled", Boolean, nullable=False, server_default=text("false")),
    )
    table(
        "solar_cycle",
        identity(),
        Column("site", String(64), nullable=False),
        Column("cycle_date", Date, nullable=False),
        instant("dawn"),
        instant("dusk"),
        instant("next_dawn"),
        Column("revision", String(64), nullable=False),
        Column("daytime_frames", Integer, nullable=False),
        Column("overnight_frames", Integer, nullable=False),
        UniqueConstraint("site", "cycle_date", "revision"),
        CheckConstraint("dawn < dusk AND dusk < next_dawn", name="boundaries"),
        CheckConstraint(
            "daytime_frames >= 0 AND overnight_frames >= 0 AND "
            "daytime_frames + overnight_frames = 7200",
            name="allocation",
        ),
    )
    table(
        "segment",
        identity(),
        Column(
            "camera_id",
            String(32),
            ForeignKey("weather.camera.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        ref("solar_cycle_id", "solar_cycle"),
        Column("kind", String(16), nullable=False),
        instant("starts_at"),
        instant("ends_at"),
        Column("frames", Integer, nullable=False),
        Column("revision", Integer, nullable=False, server_default=text("1")),
        Column("state", String(16), nullable=False, server_default=text("'capturing'")),
        data("coverage"),
        UniqueConstraint("camera_id", "id"),
        UniqueConstraint("camera_id", "solar_cycle_id", "kind", "revision"),
        CheckConstraint("kind IN ('daytime','overnight')", name="kind"),
        CheckConstraint("starts_at < ends_at", name="interval"),
        CheckConstraint("frames BETWEEN 0 AND 7200", name="frames"),
        CheckConstraint(
            "state IN ('capturing','processing','published','partial','unavailable')",
            name="state",
        ),
    )
    table(
        "capture_chunk",
        identity(),
        Column(
            "camera_id",
            String(32),
            ForeignKey("weather.camera.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        instant("utc_hour"),
        Column("pod_uid", String(128), nullable=False),
        Column("incarnation", String(128), nullable=False),
        Column("sequence", BigInteger, nullable=False),
        digest(),
        Column("path", Text, nullable=False, unique=True),
        Column("state", String(16), nullable=False, server_default=text("'sealed'")),
        Column("bytes", BigInteger, nullable=False),
        created(),
        UniqueConstraint("camera_id", "pod_uid", "incarnation", "sequence"),
        UniqueConstraint("camera_id", "id"),
        CheckConstraint(
            "state IN ('sealed','processing','processed','quarantined','deleted')",
            name="state",
        ),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$' AND bytes > 0", name="bytes_hash"),
    )
    table(
        "capture_slot",
        identity(),
        Column(
            "camera_id",
            String(32),
            ForeignKey("weather.camera.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column("chunk_id", PGUUID(as_uuid=True), nullable=False),
        instant("slot_ts"),
        instant("source_ts"),
        instant("capture_upper", True),
        Column("uncertainty_seconds", Float),
        digest(),
        Column("width", Integer, nullable=False),
        Column("height", Integer, nullable=False),
        Column("duplicates", Integer, nullable=False, server_default=text("0")),
        Column("selection_revision", String(64), nullable=False),
        ForeignKeyConstraint(
            ["camera_id", "chunk_id"],
            ["weather.capture_chunk.camera_id", "weather.capture_chunk.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("camera_id", "slot_ts"),
        UniqueConstraint("camera_id", "id"),
        CheckConstraint(
            "width > 0 AND height > 0 AND duplicates >= 0", name="geometry"
        ),
        CheckConstraint(
            "uncertainty_seconds IS NULL OR uncertainty_seconds >= 0",
            name="uncertainty",
        ),
    )

    table(
        "media_asset",
        identity(),
        Column("class", String(32), nullable=False),
        Column("bucket", String(32), nullable=False),
        Column("visibility", String(8), nullable=False),
        digest(),
        Column("extension", String(8), nullable=False),
        Column("object_key", Text, nullable=False, unique=True),
        Column("bytes", BigInteger, nullable=False),
        Column("content_type", String(128), nullable=False),
        Column("width", Integer),
        Column("height", Integer),
        instant("public_not_before", True),
        instant("published_at", True),
        Column("state", String(16), nullable=False, server_default=text("'available'")),
        created(),
        *withdrawn(),
        UniqueConstraint("id", "class"),
        CheckConstraint(
            "bucket IN ('weather-media','weather-originals') AND "
            "visibility IN ('public','private')",
            name="bucket_visibility",
        ),
        CheckConstraint(
            "(class = 'original') = (bucket = 'weather-originals') AND "
            "(class = 'original') = (visibility = 'private')",
            name="original_private",
        ),
        CheckConstraint(
            "class = 'original' OR public_not_before IS NOT NULL", name="embargo"
        ),
        CheckConstraint(
            "class <> 'original' OR published_at IS NULL", name="original_not_published"
        ),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$' AND extension ~ '^[a-z0-9]{1,8}$' AND "
            "class ~ '^[a-z][a-z0-9_]*$' AND bytes > 0",
            name="identity",
        ),
        CheckConstraint(
            "object_key = class || '/' || sha256 || '.' || extension",
            name="content_address",
        ),
        CheckConstraint(
            "state IN ('available','unavailable','tombstoned')", name="state"
        ),
    )
    table(
        "video",
        identity(),
        ref("segment_id", "segment"),
        Column("revision", Integer, nullable=False),
        Column("state", String(16), nullable=False),
        data("time_map"),
        data("quality"),
        *[
            ref(f"{role}_asset_id", "media_asset", True)
            for role in ("native", "web", "mobile", "poster", "sidecar")
        ],
        created(),
        *withdrawn(),
        UniqueConstraint("id", "revision"),
        UniqueConstraint("segment_id", "revision"),
        CheckConstraint("revision > 0", name="revision"),
        CheckConstraint(
            "state IN ('processing','published','partial','unavailable','expired')",
            name="state",
        ),
    )
    publication = table(
        "publication",
        identity(),
        Column("video_id", PGUUID(as_uuid=True), nullable=False),
        Column("revision", Integer, nullable=False),
        Column("generation", BigInteger, nullable=False),
        Column("current", Boolean, nullable=False, server_default=text("true")),
        instant("public_not_before"),
        Column(
            "committed_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
        ),
        ForeignKeyConstraint(
            ["video_id", "revision"],
            ["weather.video.id", "weather.video.revision"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("video_id", "generation"),
        CheckConstraint("generation > 0", name="generation"),
    )
    Index(
        "uq_publication_current",
        publication.c.video_id,
        unique=True,
        postgresql_where=publication.c.current.is_(True),
    )
    table(
        "keogram",
        identity(),
        ref("segment_id", "segment"),
        ref("asset_id", "media_asset"),
        Column("revision", Integer, nullable=False),
        data("utc_bin_map"),
        digest("lineage_sha256"),
        Column("state", String(16), nullable=False),
        UniqueConstraint("segment_id", "revision"),
    )
    still = table(
        "archival_still",
        identity(),
        Column(
            "camera_id",
            String(32),
            ForeignKey("weather.camera.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        ref("weather_day_id", "weather_day"),
        Column("hour_slot_id", PGUUID(as_uuid=True), nullable=False),
        Column("capture_slot_id", PGUUID(as_uuid=True), nullable=False),
        Column("source_segment_id", PGUUID(as_uuid=True), nullable=False),
        ref("source_video_id", "video", True),
        Column("original_asset_id", PGUUID(as_uuid=True), nullable=False),
        Column(
            "original_class",
            String(32),
            nullable=False,
            server_default=text("'original'"),
        ),
        Column("revision", Integer, nullable=False),
        Column("state", String(16), nullable=False, server_default=text("'active'")),
        instant("source_ts"),
        Column("uncertainty_seconds", Float),
        Column("selection_policy", String(64), nullable=False),
        created(),
        ForeignKeyConstraint(
            ["weather_day_id", "hour_slot_id"],
            [
                "weather.archive_hour_slot.weather_day_id",
                "weather.archive_hour_slot.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["camera_id", "capture_slot_id"],
            ["weather.capture_slot.camera_id", "weather.capture_slot.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["camera_id", "source_segment_id"],
            ["weather.segment.camera_id", "weather.segment.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["original_asset_id", "original_class"],
            ["weather.media_asset.id", "weather.media_asset.class"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("camera_id", "hour_slot_id", "revision"),
        CheckConstraint("original_class = 'original'", name="original"),
        CheckConstraint("state IN ('active','retired') AND revision > 0", name="state"),
    )
    Index(
        "uq_archival_still_active",
        still.c.camera_id,
        still.c.hour_slot_id,
        unique=True,
        postgresql_where=still.c.state == "active",
    )
    table(
        "still_derivative",
        identity(),
        ref("archival_still_id", "archival_still"),
        Column("asset_id", PGUUID(as_uuid=True), nullable=False),
        Column("asset_class", String(32), nullable=False),
        Column("recipe_revision", String(64), nullable=False),
        Column("state", String(16), nullable=False),
        instant("committed_at", True),
        ForeignKeyConstraint(
            ["asset_id", "asset_class"],
            ["weather.media_asset.id", "weather.media_asset.class"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("asset_class <> 'original'", name="public_class"),
    )
    table(
        "slot_camera_state",
        Column(
            "camera_id",
            String(32),
            ForeignKey("weather.camera.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        Column(
            "hour_slot_id",
            PGUUID(as_uuid=True),
            ForeignKey("weather.archive_hour_slot.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        Column("state", String(16), nullable=False),
        Column("reason", String(128)),
        CheckConstraint(
            "state IN ('pending','finalized','missing','unavailable')", name="state"
        ),
    )
    table(
        "camera_preview",
        identity(),
        Column(
            "camera_id",
            String(32),
            ForeignKey("weather.camera.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column("generation", BigInteger, nullable=False),
        Column("bytes", BigInteger, nullable=False),
        Column("width", Integer, nullable=False),
        Column("height", Integer, nullable=False),
        instant("captured_lower"),
        instant("captured_upper"),
        Column("path", Text, nullable=False, unique=True),
        digest(),
        instant("public_not_before"),
        instant("expires_at"),
        instant("first_published_at", True),
        data("policy"),
        Column("timing_trusted", Boolean, nullable=False, server_default=text("false")),
        UniqueConstraint("camera_id", "generation"),
        CheckConstraint(
            "captured_lower <= captured_upper AND "
            "public_not_before >= captured_upper + interval '630 seconds' AND "
            "expires_at > public_not_before AND (first_published_at IS NULL OR "
            "(first_published_at >= public_not_before AND first_published_at < expires_at))",
            name="timing",
        ),
        CheckConstraint(
            "path LIKE 'previews/' || camera_id || '/%' AND path !~ '(^|/)\\.\\.(/|$)'",
            name="path",
        ),
        CheckConstraint(
            "bytes > 0 AND width > 0 AND height > 0 AND generation > 0 AND "
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="geometry",
        ),
    )

    table(
        "weather_event",
        identity(),
        ref("weather_day_id", "weather_day"),
        Column("logical_key", Text, nullable=False, unique=True),
        instant("starts_at"),
        instant("ends_at"),
        Column("origin", String(16), nullable=False),
        Column("basis", String(16), nullable=False),
        Column("revision", Integer, nullable=False),
        data("evidence"),
        Column("published", Boolean, nullable=False, server_default=text("false")),
        Column("withdrawn", Boolean, nullable=False, server_default=text("false")),
        CheckConstraint("starts_at <= ends_at AND revision > 0", name="interval"),
        CheckConstraint(
            "(origin='measured' AND basis='instrument') OR "
            "(origin='visual' AND basis='visual') OR "
            "(origin='synthesis' AND basis='inference') OR "
            "(origin IN ('human','manual') AND basis IN "
            "('instrument','visual','inference'))",
            name="provenance",
        ),
    )
    table(
        "event_video_link",
        ref("event_id", "weather_event"),
        ref("video_id", "video"),
        Column("source_start", DateTime(timezone=True), nullable=False),
        Column("source_end", DateTime(timezone=True), nullable=False),
        Column("seek_seconds", Float),
        UniqueConstraint("event_id", "video_id"),
        CheckConstraint(
            "source_start <= source_end AND "
            "(seek_seconds IS NULL OR seek_seconds >= 0)",
            name="interval",
        ),
    )
    table(
        "editorial_target",
        Column("target", String(256), primary_key=True),
        Column(
            "current_revision", BigInteger, nullable=False, server_default=text("0")
        ),
    )
    table("operator_allowlist", Column("subject", String(256), primary_key=True))
    table(
        "editorial_revision",
        identity(),
        Column(
            "target",
            String(256),
            ForeignKey("weather.editorial_target.target", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column("revision_no", BigInteger, nullable=False),
        Column("actor", String(256), nullable=False),
        data("changes"),
        Column("reason", Text, nullable=False),
        created(),
        UniqueConstraint("target", "revision_no"),
        CheckConstraint(
            "revision_no > 0 AND jsonb_typeof(changes)='object'", name="revision"
        ),
    )
    table(
        "tag_assignment",
        identity(),
        Column(
            "target",
            String(256),
            ForeignKey("weather.editorial_target.target", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column("tag", String(64), nullable=False),
        Column("public", Boolean, nullable=False, server_default=text("false")),
        UniqueConstraint("target", "tag"),
    )
    table(
        "operator_favorite",
        identity(),
        Column(
            "target",
            String(256),
            ForeignKey("weather.editorial_target.target", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column("subject", String(256), nullable=False),
        UniqueConstraint("target", "subject"),
    )
    profile = table(
        "embedding_profile",
        Column("id", String(64), primary_key=True),
        Column("model", String(128), nullable=False),
        Column("dimensions", Integer, nullable=False, server_default=text("2560")),
        Column("active", Boolean, nullable=False, server_default=text("false")),
        CheckConstraint("dimensions=2560", name="dimensions"),
    )
    Index(
        "uq_embedding_profile_active",
        profile.c.active,
        unique=True,
        postgresql_where=profile.c.active.is_(True),
    )
    table(
        "search_document",
        identity(),
        ref("video_id", "video"),
        Column(
            "profile_id",
            String(64),
            ForeignKey("weather.embedding_profile.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column("revision", Integer, nullable=False),
        Column("text", Text, nullable=False),
        Column("lexical", TSVECTOR),
        Column("embedding", Vector()),
        Column("current", Boolean, nullable=False, server_default=text("false")),
        UniqueConstraint("video_id", "profile_id", "revision"),
    )

    job = table(
        "job",
        identity(),
        Column("kind", String(128), nullable=False),
        Column("idempotency_key", String(256), nullable=False, unique=True),
        data(),
        Column("expected_revision", BigInteger),
        Column("status", String(16), nullable=False, server_default=text("'queued'")),
        Column("attempt", Integer, nullable=False, server_default=text("0")),
        instant("not_before"),
        instant("deadline"),
        Column("lease_owner", String(128)),
        instant("lease_until", True),
        Column("fencing_token", BigInteger, nullable=False, server_default=text("0")),
        Column("last_error", String(128)),
        Column("result", JSONB),
        created(),
        CheckConstraint("attempt >= 0 AND fencing_token >= 0", name="counters"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled','expired')",
            name="status",
        ),
        CheckConstraint(
            "(status='running') = (lease_owner IS NOT NULL AND "
            "lease_until IS NOT NULL)",
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
    table(
        "outbox",
        identity(),
        Column("topic", String(128), nullable=False),
        Column("idempotency_key", String(256), nullable=False, unique=True),
        data(),
        Column("expected_revision", BigInteger),
        instant("not_before"),
        instant("deadline"),
        instant("dispatched_at", True),
        ref("job_id", "job", True),
        created(),
    )
    return metadata
