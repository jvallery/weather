# SPDX-License-Identifier: MIT
"""Public API views for #14: one typed, gated view per current family plus the station alias.

Expand-only on top of the frozen baseline. Inline DDL; never import application modules here.
A value is public only when its row is valid, the field is not listed invalid and it is a JSON
number. The views read only through the existing ix_obs_st_source_ts and
ix_rapid_wind_source_ts btrees, so the API's newest-row and keyset queries never scan history.

They are deliberately not security_barrier views: they have no row filter (gating happens per
column in the select list, and raw values/quality are not exposed), so a barrier protects
nothing, while it would stop the planner flattening the view and using the btree for
ORDER BY ... LIMIT.
"""

from alembic import op

revision = "0002_api"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

# Must match weather.api.registry.COLUMNS (a unit test compares them).
COLUMNS = {
    "obs_st": (
        "air_temperature",
        "illuminance",
        "lightning_strike_avg_distance",
        "lightning_strike_count",
        "precip_accumulated",
        "relative_humidity",
        "solar_radiation",
        "station_pressure",
        "uv",
        "wind_avg",
        "wind_direction",
        "wind_gust",
        "wind_lull",
    ),
    "rapid_wind": ("wind_direction", "wind_speed"),
}
VALID = (
    "coalesce(o.quality->'valid'='true'::jsonb,false)"
    " AND jsonb_typeof(o.quality->'invalid')='array'"
)


def view(family: str) -> str:
    fields = ",".join(
        f"""CASE WHEN {VALID} AND NOT (o.quality->'invalid') ? '{name}'
              AND jsonb_typeof(o."values"->'{name}')='number'
             THEN (o."values"->>'{name}')::double precision END AS {name}"""
        for name in COLUMNS[family]
    )
    return f"""CREATE VIEW public_api.{family} AS
        SELECT o.id,o.source_ts AS observed_at,r.received_at,{VALID} AS valid,{fields}
        FROM weather.{family} o JOIN weather.telemetry_receipt r ON r.id=o.receipt_id"""


def statements() -> list[str]:
    return [
        view("obs_st"),
        view("rapid_wind"),
        "CREATE VIEW public_api.station AS SELECT alias FROM weather.station",
        "REVOKE ALL ON public_api.obs_st,public_api.rapid_wind,public_api.station FROM PUBLIC",
        "GRANT SELECT ON public_api.obs_st,public_api.rapid_wind,public_api.station TO weather_api",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
