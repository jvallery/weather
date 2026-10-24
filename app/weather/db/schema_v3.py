# SPDX-License-Identifier: MIT
"""Immutable migration-0003 metadata; compose after v1 and telemetry v2."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

PUBLIC_FAMILIES = ("obs_st", "rapid_wind", "evt_precip", "evt_strike", "obs_air", "obs_sky")
ALL_FAMILIES = (*PUBLIC_FAMILIES, "device_status", "hub_status")
EVIDENCE_TABLES = (
    "telemetry_receipt",
    "receipt_transport",
    "receipt_row_provenance",
    "receipt_integrity_incident",
    "observation_conflict",
    "telemetry_coverage_interval",
    "telemetry_loss_snapshot",
)
TABLE_NAMES = (
    "telemetry_publication_clock",
    "telemetry_publication",
    "metric_binding",
    "metric_current",
)

# Public IDs and source fields from the code-first registry at ab637c7. Derived
# rain_last_hour/rain_today have no direct observation pointer and are absent.
BINDINGS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("temperature", "air_temperature", "degC", ("obs_st", "obs_air")),
    ("humidity", "relative_humidity", "%", ("obs_st", "obs_air")),
    ("wind_lull", "wind_lull", "m/s", ("obs_st", "obs_sky")),
    ("wind_avg", "wind_avg", "m/s", ("obs_st", "obs_sky")),
    ("wind_speed", "wind_speed", "m/s", ("rapid_wind",)),
    ("wind_gust", "wind_gust", "m/s", ("obs_st", "obs_sky")),
    ("wind_direction", "wind_direction", "deg", ("obs_st", "obs_sky", "rapid_wind")),
    ("precipitation", "precip_accumulated", "mm", ("obs_st", "obs_sky")),
    ("station_pressure", "station_pressure", "hPa", ("obs_st", "obs_air")),
    ("illuminance", "illuminance", "lx", ("obs_st", "obs_sky")),
    ("uv", "uv", "index", ("obs_st", "obs_sky")),
    ("irradiance", "solar_radiation", "W/m2", ("obs_st", "obs_sky")),
    ("lightning_count", "lightning_strike_count", "1", ("obs_st", "obs_air")),
    ("lightning_distance", "lightning_strike_avg_distance", "km", ("obs_st", "obs_air")),
)


def extend_metadata(metadata: MetaData) -> None:
    Table(
        "telemetry_publication_clock",
        metadata,
        Column("singleton", Boolean, primary_key=True),
        Column("published_through", BigInteger, nullable=False, server_default=text("0")),
        CheckConstraint("singleton", name="singleton"),
        CheckConstraint("published_through >= 0", name="nonnegative"),
        schema="weather",
    )
    Table(
        "telemetry_publication",
        metadata,
        Column("publication_seq", BigInteger, primary_key=True, autoincrement=False),
        Column("family", String(32), nullable=False),
        Column("observation_id", UUID(as_uuid=True), nullable=False),
        UniqueConstraint("family", "observation_id"),
        CheckConstraint("publication_seq > 0", name="positive"),
        CheckConstraint(
            "family IN ('obs_st','rapid_wind','evt_precip','evt_strike','obs_air','obs_sky')",
            name="family",
        ),
        schema="weather",
    )
    Table(
        "metric_binding",
        metadata,
        Column("metric", String(64), primary_key=True),
        Column("family", String(32), primary_key=True),
        Column("field", String(64), nullable=False),
        Column("canonical_unit", String(32), nullable=False),
        schema="weather",
    )
    Table(
        "metric_current",
        metadata,
        Column(
            "station_id",
            UUID(as_uuid=True),
            ForeignKey("weather.station.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        Column("metric", String(64), primary_key=True),
        Column("family", String(32), nullable=False),
        Column("observation_id", UUID(as_uuid=True), nullable=False),
        Column("source_ts", DateTime(timezone=True), nullable=False),
        Column("revision", BigInteger, nullable=False, server_default=text("1")),
        ForeignKeyConstraint(
            ["metric", "family"],
            ["weather.metric_binding.metric", "weather.metric_binding.family"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["family", "observation_id"],
            [
                "weather.telemetry_publication.family",
                "weather.telemetry_publication.observation_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision > 0", name="positive_revision"),
        schema="weather",
    )
