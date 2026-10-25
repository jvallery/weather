# SPDX-License-Identifier: MIT
"""Frozen solar schedule and per-camera render segments (#15).

Expand-only and inline DDL; never import application modules here. `solar_cycle` is keyed on
(site, cycle_date) and frozen once written; the private site hash is provenance only. Each
cycle has one `segment` row per camera and kind. A clock correction adds a new revision rather
than editing a row, and no two current segments of one camera may overlap in time.
"""

from alembic import op

revision = "0006_render"
down_revision = "0005_events"
branch_labels = None
depends_on = None

CAMERAS = "'skycam_east','skycam_west','frontyard_1'"
STATES = "'scheduled','processing','published','partial','unavailable'"


def statements() -> list[str]:
    return [
        # Trusted extension; the migrator owns the database. Needed for camera equality in the
        # overlap exclusion.
        "CREATE EXTENSION IF NOT EXISTS btree_gist",
        """CREATE TABLE weather.solar_cycle (
            site varchar(32) NOT NULL,
            cycle_date date NOT NULL,
            dawn timestamptz NOT NULL,
            dusk timestamptz NOT NULL,
            next_dawn timestamptz NOT NULL,
            day_frames integer NOT NULL,
            night_frames integer NOT NULL,
            provenance_hash varchar(64) NOT NULL,
            astral_version varchar(16) NOT NULL,
            tzdata_version varchar(16) NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_solar_cycle PRIMARY KEY (site, cycle_date),
            CONSTRAINT fk_solar_cycle_cycle_date_weather_day FOREIGN KEY (cycle_date)
              REFERENCES weather.weather_day (local_date) ON DELETE RESTRICT,
            CONSTRAINT ck_solar_cycle_order CHECK (site ~ '^[a-z][a-z0-9_]{0,31}$'
              AND dawn < dusk AND dusk < next_dawn),
            CONSTRAINT ck_solar_cycle_allocation CHECK (day_frames > 0 AND night_frames > 0
              AND day_frames + night_frames = 7200),
            CONSTRAINT ck_solar_cycle_provenance CHECK (provenance_hash ~ '^[0-9a-f]{64}$')
        )""",
        f"""CREATE TABLE weather.segment (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            site varchar(32) NOT NULL,
            cycle_date date NOT NULL,
            camera_id varchar(32) NOT NULL,
            kind varchar(16) NOT NULL,
            revision integer DEFAULT 1 NOT NULL,
            current boolean DEFAULT true NOT NULL,
            starts_at timestamptz NOT NULL,
            ends_at timestamptz NOT NULL,
            frames integer NOT NULL,
            state varchar(16) DEFAULT 'scheduled' NOT NULL,
            reason varchar(64),
            slot_coverage double precision,
            mezzanine_through timestamptz,
            mezzanine_durable_at timestamptz,
            render_result jsonb,
            finalized_at timestamptz,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_segment PRIMARY KEY (id),
            CONSTRAINT uq_segment_camera_id_cycle_date_kind_revision
              UNIQUE (camera_id, cycle_date, kind, revision),
            CONSTRAINT fk_segment_site_solar_cycle FOREIGN KEY (site, cycle_date)
              REFERENCES weather.solar_cycle (site, cycle_date) ON DELETE RESTRICT,
            CONSTRAINT ck_segment_identity CHECK (camera_id IN ({CAMERAS})
              AND kind IN ('daytime','overnight') AND revision > 0),
            CONSTRAINT ck_segment_interval CHECK (starts_at < ends_at
              AND frames BETWEEN 1 AND 7200),
            CONSTRAINT ck_segment_state CHECK (state IN ({STATES})
              AND (state IN ('partial','unavailable')) = (reason IS NOT NULL)),
            CONSTRAINT ck_segment_coverage CHECK (slot_coverage IS NULL
              OR (slot_coverage >= 0 AND slot_coverage <= 1)),
            CONSTRAINT ck_segment_result CHECK (render_result IS NULL
              OR (jsonb_typeof(render_result) = 'object'
                  AND octet_length(render_result::text) <= 1048576)),
            CONSTRAINT ex_segment_current_overlap EXCLUDE USING gist
              (camera_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&) WHERE (current)
        )""",
        """CREATE UNIQUE INDEX uq_segment_current
           ON weather.segment (camera_id, cycle_date, kind) WHERE current""",
        """CREATE INDEX ix_segment_ends_at ON weather.segment (ends_at) WHERE current""",
        "GRANT SELECT,INSERT ON weather.solar_cycle,weather.segment TO weather_worker",
        "GRANT UPDATE (state,reason,slot_coverage,mezzanine_through,mezzanine_durable_at,"
        "render_result,finalized_at,updated_at) ON weather.segment TO weather_worker",
        # The sidecar weather block reads the same public telemetry views as the API (#14).
        "GRANT USAGE ON SCHEMA public_api TO weather_worker",
        "GRANT SELECT ON public_api.obs_st,public_api.rapid_wind TO weather_worker",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
