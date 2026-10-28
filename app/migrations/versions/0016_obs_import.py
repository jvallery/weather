# SPDX-License-Identifier: MIT
"""Imported station history (#167): Tempest observations from the verdify-platform database.

Expand-only on top of 0015_catalog_thumbnails. Inline DDL; never import application modules here.

`obs_import` holds imported `obs_st`-shaped readings (canonical metric units, the same JSON keys as
live `obs_st.values`), each with its source, its import batch and its support cap: the source
reported every one to five minutes, so a reading is held at most `cap_seconds` (live readings keep
the registry's two-interval cap). `import_batch` records every batch: the cutoff (the first live
report at import time), the source row count, inserted, overlapping and duplicate rows.

The live views `public_api.obs_st` and `public_api.rapid_wind` are unchanged (current
conditions, the raw export and the history start keep their btree plans). Aggregate reads use
`public_api.obs_st_history`: the live view's rows with `cap_seconds` null and `provenance` 'live',
plus the imported rows, shown only before the first live report. Live wins, so live `obs_st` and
its aggregates are unchanged for every period the receiver covers. `public_api.rapid_wind_history`
is the live rapid-wind view with the same two columns. `public_api.imported_history` gives the
imported range in two index probes. Imported rows carry no receipt (`received_at` is null) and are
never mistaken for live receipts.
"""

from alembic import op

revision = "0016_obs_import"
down_revision = "0015_catalog_thumbnails"
branch_labels = None
depends_on = None

# Must match 0002_api.COLUMNS (and weather.api.registry.COLUMNS).
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
SOURCES = "'verdify-platform'"


def live(family: str) -> str:
    return f"""SELECT o.*,CAST(NULL AS integer) AS cap_seconds,
          CAST('live' AS varchar(32)) AS provenance
        FROM public_api.{family} o"""


CUTOFF = "coalesce((SELECT min(source_ts) FROM weather.obs_st),'infinity'::timestamptz)"


def imported() -> str:
    fields = ",".join(
        f"""CASE WHEN jsonb_typeof(i."values"->'{name}')='number'
             THEN (i."values"->>'{name}')::double precision END AS {name}"""
        for name in COLUMNS["obs_st"]
    )
    return f"""SELECT i.id,i.source_ts AS observed_at,CAST(NULL AS timestamptz) AS received_at,
          i.imported_at AS inserted_at,true AS valid,'{{}}'::text[] AS invalid_fields,{fields},
          i.cap_seconds,i.source AS provenance
        FROM weather.obs_import i WHERE i.source_ts < {CUTOFF}"""


def statements() -> list[str]:
    return [
        f"""CREATE TABLE weather.import_batch (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            source varchar(32) NOT NULL,
            started_at timestamptz DEFAULT clock_timestamp() NOT NULL,
            finished_at timestamptz,
            source_start timestamptz NOT NULL,
            source_end timestamptz NOT NULL,
            cutoff timestamptz NOT NULL,
            source_rows integer,
            inserted_rows integer,
            overlap_rows integer,
            duplicate_rows integer,
            state varchar(16) DEFAULT 'running' NOT NULL,
            CONSTRAINT pk_import_batch PRIMARY KEY (id),
            CONSTRAINT ck_import_batch_source CHECK (source IN ({SOURCES})),
            CONSTRAINT ck_import_batch_state CHECK (state IN ('running','complete','rolled_back')),
            CONSTRAINT ck_import_batch_window CHECK (source_start < source_end)
        )""",
        f"""CREATE TABLE weather.obs_import (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            batch_id uuid NOT NULL,
            source varchar(32) NOT NULL,
            source_ts timestamptz NOT NULL,
            "values" jsonb NOT NULL,
            cap_seconds integer NOT NULL,
            imported_at timestamptz DEFAULT clock_timestamp() NOT NULL,
            CONSTRAINT pk_obs_import PRIMARY KEY (id),
            CONSTRAINT fk_obs_import_batch_id_import_batch FOREIGN KEY (batch_id)
              REFERENCES weather.import_batch(id),
            CONSTRAINT uq_obs_import_source_source_ts UNIQUE (source, source_ts),
            CONSTRAINT ck_obs_import_source CHECK (source IN ({SOURCES})),
            CONSTRAINT ck_obs_import_cap CHECK (cap_seconds BETWEEN 60 AND 3600),
            CONSTRAINT ck_obs_import_values CHECK (jsonb_typeof("values") = 'object')
        )""",
        "CREATE INDEX ix_obs_import_source_ts ON weather.obs_import (source_ts)",
        "CREATE INDEX ix_obs_import_batch_id ON weather.obs_import (batch_id)",
        f"CREATE VIEW public_api.obs_st_history AS {live('obs_st')} UNION ALL {imported()}",
        f"CREATE VIEW public_api.rapid_wind_history AS {live('rapid_wind')}",
        f"""CREATE VIEW public_api.imported_history AS
            SELECT i.source,i.start,i.last FROM (
              SELECT CAST('verdify-platform' AS varchar(32)) AS source,
                (SELECT min(source_ts) FROM weather.obs_import
                  WHERE source_ts < {CUTOFF}) AS start,
                (SELECT max(source_ts) FROM weather.obs_import
                  WHERE source_ts < {CUTOFF}) AS last) i
            WHERE i.start IS NOT NULL""",
        # Only the migrator (owner) loads and rolls back batches; readers use the views.
        "REVOKE ALL ON weather.import_batch,weather.obs_import FROM PUBLIC",
        "REVOKE ALL ON public_api.obs_st_history,public_api.rapid_wind_history,"
        "public_api.imported_history FROM PUBLIC",
        # The api and the worker (the pipeline's station summaries) read aggregates.
        "GRANT SELECT ON public_api.obs_st_history,public_api.rapid_wind_history,"
        "public_api.imported_history TO weather_api,weather_worker",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
