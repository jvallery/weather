# SPDX-License-Identifier: MIT
"""Imported rapid-wind samples (#167): `obs_import` rows gain a family.

Expand-only on top of 0016_obs_import. Inline DDL; never import application modules here.

The verdify-platform source also holds the Tempest's rapid-wind samples (3 s speed and
direction) on each station report. `obs_import.family` ('obs_st', the default for every existing
row, or 'rapid_wind') says which history a row belongs to; uniqueness is per source, family and
source time. The cap may now be as short as the live rapid cadence (a 3 s sample is held at most
6 s, never stretched across the minutes between reports).

`public_api.obs_st_history` keeps its columns and reads only 'obs_st' imported rows.
`public_api.rapid_wind_history` becomes the live view plus the imported 'rapid_wind' rows, shown
only before the first live rapid-wind report (live wins), with the same appended columns.
"""

from alembic import op

revision = "0017_obs_import_rapid"
down_revision = "0016_obs_import"
branch_labels = None
depends_on = None

# Must match weather.api.registry.COLUMNS.
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


def history(family: str) -> str:
    fields = ",".join(
        f"""CASE WHEN jsonb_typeof(i."values"->'{name}')='number'
             THEN (i."values"->>'{name}')::double precision END AS {name}"""
        for name in COLUMNS[family]
    )
    cutoff = (
        f"coalesce((SELECT min(source_ts) FROM weather.{family}),'infinity'::timestamptz)"
    )
    return f"""CREATE OR REPLACE VIEW public_api.{family}_history AS
        SELECT o.*,CAST(NULL AS integer) AS cap_seconds,
          CAST('live' AS varchar(32)) AS provenance
        FROM public_api.{family} o
        UNION ALL
        SELECT i.id,i.source_ts AS observed_at,CAST(NULL AS timestamptz) AS received_at,
          i.imported_at AS inserted_at,true AS valid,'{{}}'::text[] AS invalid_fields,{fields},
          i.cap_seconds,i.source AS provenance
        FROM weather.obs_import i
        WHERE i.family = '{family}' AND i.source_ts < {cutoff}"""


def statements() -> list[str]:
    return [
        "ALTER TABLE weather.obs_import ADD COLUMN family varchar(16) DEFAULT 'obs_st' NOT NULL",
        "ALTER TABLE weather.obs_import ADD CONSTRAINT ck_obs_import_family"
        " CHECK (family IN ('obs_st','rapid_wind'))",
        "ALTER TABLE weather.obs_import ADD CONSTRAINT uq_obs_import_source_family_source_ts"
        " UNIQUE (source, family, source_ts)",
        "ALTER TABLE weather.obs_import DROP CONSTRAINT uq_obs_import_source_source_ts",
        "ALTER TABLE weather.obs_import DROP CONSTRAINT ck_obs_import_cap",
        "ALTER TABLE weather.obs_import ADD CONSTRAINT ck_obs_import_cap"
        " CHECK (cap_seconds BETWEEN 1 AND 3600)",
        history("obs_st"),
        history("rapid_wind"),
        # The imported range is the station-report history's.
        """CREATE OR REPLACE VIEW public_api.imported_history AS
            SELECT i.source,i.start,i.last FROM (
              SELECT CAST('verdify-platform' AS varchar(32)) AS source,
                (SELECT min(source_ts) FROM weather.obs_import WHERE family='obs_st'
                  AND source_ts < coalesce((SELECT min(source_ts) FROM weather.obs_st),
                                           'infinity'::timestamptz)) AS start,
                (SELECT max(source_ts) FROM weather.obs_import WHERE family='obs_st'
                  AND source_ts < coalesce((SELECT min(source_ts) FROM weather.obs_st),
                                           'infinity'::timestamptz)) AS last) i
            WHERE i.start IS NOT NULL""",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
