# SPDX-License-Identifier: MIT
"""Hourly private originals and sanitized comparison stills (#38).

Expand-only and inline DDL; never import application modules here. `hour_slot` is keyed by its
UTC start and carries the `weather_day` FK, so a local day has 23, 24 or 25 slots; a trigger
keeps every slot inside its day's bounds (the pinned Python time model writes both). An
`archival_still` row is one selection revision per (camera, slot): the archived native original
or an explicit missing slot, bound to its slot by a same-day composite FK, to its #15 segment
and solar cycle, and to the private original through #18's `media_asset` (class, hash). At most
one revision per (camera, slot) is active. `still_derivative` lists the sanitized public
images. `public_api.stills` never exposes the original, its hash or its capture location, and
never shows a selection before its capture upper bound + 630 s.
"""

from alembic import op

revision = "0009_stills"
down_revision = "0006_render"
branch_labels = None
depends_on = None

CAMERAS = "'skycam_east','skycam_west','frontyard_1'"
# Every column a selected (non-missing) row must carry, and a missing row must not.
CAPTURE = (
    "attempt_id,chunk,member,source_ts,capture_upper,offset_us,cycle_date,segment_kind,"
    "segment_revision,original_asset_id,original_class,sha256,bytes,width,height,publication_id,"
    "publication_product"
)
# The still product key, e.g. 2026-11-01T07:00Z; the colon is escaped from SQLAlchemy binds.
STILL_KEY = """to_char(s.slot_starts_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24"\\:00Z"')"""


def statements() -> list[str]:
    return [
        # Lets a still row prove its recorded hash is the archived original's (#18 catalog).
        """ALTER TABLE weather.media_asset
           ADD CONSTRAINT uq_media_asset_id_class_sha256 UNIQUE (id, class, sha256)""",
        # Lets a still row prove its publication is the same camera's still product.
        """ALTER TABLE weather.publication
           ADD CONSTRAINT uq_publication_id_product_camera_id UNIQUE (id, product, camera_id)""",
        """CREATE TABLE weather.hour_slot (
            starts_at timestamptz NOT NULL,
            local_date date NOT NULL,
            utc_offset_minutes smallint NOT NULL,
            fold smallint NOT NULL,
            CONSTRAINT pk_hour_slot PRIMARY KEY (starts_at),
            CONSTRAINT uq_hour_slot_starts_at_local_date UNIQUE (starts_at, local_date),
            CONSTRAINT fk_hour_slot_local_date_weather_day FOREIGN KEY (local_date)
              REFERENCES weather.weather_day (local_date) ON DELETE RESTRICT,
            CONSTRAINT ck_hour_slot_identity CHECK (mod(extract(epoch FROM starts_at), 3600) = 0
              AND utc_offset_minutes IN (-420,-360) AND fold IN (0,1))
        )""",
        """CREATE FUNCTION weather.check_hour_slot() RETURNS trigger
           LANGUAGE plpgsql SET search_path=pg_catalog AS $$
           BEGIN
             IF NOT EXISTS (SELECT 1 FROM weather.weather_day d WHERE d.local_date=NEW.local_date
                 AND NEW.starts_at >= d.starts_at
                 AND NEW.starts_at + interval '1 hour' <= d.ends_at) THEN
               RAISE EXCEPTION 'hour slot is outside its calendar day' USING ERRCODE='23514';
             END IF;
             RETURN NEW;
           END $$""",
        """CREATE TRIGGER hour_slot_in_day BEFORE INSERT OR UPDATE ON weather.hour_slot
           FOR EACH ROW EXECUTE FUNCTION weather.check_hour_slot()""",
        f"""CREATE TABLE weather.archival_still (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            camera_id varchar(32) NOT NULL,
            slot_starts_at timestamptz NOT NULL,
            local_date date NOT NULL,
            revision integer NOT NULL,
            active boolean DEFAULT true NOT NULL,
            state varchar(16) NOT NULL,
            reason varchar(64),
            policy varchar(64) NOT NULL,
            eligible integer NOT NULL,
            rejections jsonb DEFAULT '{{}}'::jsonb NOT NULL,
            attempt_id varchar(128),
            chunk varchar(256),
            member varchar(128),
            source_ts timestamptz,
            capture_upper timestamptz,
            offset_us bigint,
            cycle_date date,
            segment_kind varchar(16),
            segment_revision integer,
            original_asset_id uuid,
            original_class varchar(16),
            sha256 varchar(64),
            bytes bigint,
            width integer,
            height integer,
            publication_id uuid,
            publication_product varchar(16),
            retain_until date NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            retired_at timestamptz,
            CONSTRAINT pk_archival_still PRIMARY KEY (id),
            CONSTRAINT uq_archival_still_camera_id_slot_starts_at_revision
              UNIQUE (camera_id, slot_starts_at, revision),
            -- Same-day composite FK: the slot must belong to the row's calendar day.
            CONSTRAINT fk_archival_still_slot_starts_at_hour_slot
              FOREIGN KEY (slot_starts_at, local_date)
              REFERENCES weather.hour_slot (starts_at, local_date) ON DELETE RESTRICT,
            -- The winner's actual segment, of the same camera, and its solar cycle.
            CONSTRAINT fk_archival_still_camera_id_segment
              FOREIGN KEY (camera_id, cycle_date, segment_kind, segment_revision)
              REFERENCES weather.segment (camera_id, cycle_date, kind, revision)
              ON DELETE RESTRICT,
            CONSTRAINT fk_archival_still_original_asset_id_media_asset
              FOREIGN KEY (original_asset_id, original_class, sha256)
              REFERENCES weather.media_asset (id, class, sha256) ON DELETE RESTRICT,
            -- The publication that archived it is this camera's still product.
            CONSTRAINT fk_archival_still_publication_id_publication
              FOREIGN KEY (publication_id, publication_product, camera_id)
              REFERENCES weather.publication (id, product, camera_id) ON DELETE RESTRICT,
            CONSTRAINT ck_archival_still_identity CHECK (camera_id IN ({CAMERAS})
              AND revision > 0 AND policy ~ '^[a-z0-9][a-z0-9./_-]{{0,63}}$' AND eligible >= 0
              AND jsonb_typeof(rejections) = 'object'
              AND octet_length(rejections::text) <= 4096),
            CONSTRAINT ck_archival_still_state CHECK (
              state IN ('on_target','off_target','missing')
              AND (state = 'missing') = (reason IS NOT NULL)
              AND (reason IS NULL OR reason ~ '^[a-z][a-z0-9_]{{0,63}}$')),
            CONSTRAINT ck_archival_still_capture CHECK (CASE WHEN state = 'missing'
              THEN num_nonnulls({CAPTURE}) = 0 ELSE num_nulls({CAPTURE}) = 0 END),
            -- Never borrowed from another hour; on target means within 60 s of half past.
            CONSTRAINT ck_archival_still_hour CHECK (source_ts IS NULL OR (
              source_ts >= slot_starts_at AND source_ts < slot_starts_at + interval '1 hour'
              AND capture_upper >= source_ts
              AND offset_us = (extract(epoch FROM source_ts - slot_starts_at) * 1000000)::bigint
                - 1800000000
              AND (state = 'on_target') = (abs(offset_us) <= 60000000)
              AND original_class = 'original' AND sha256 ~ '^[0-9a-f]{{64}}$' AND bytes > 0
              AND width > 0 AND height > 0 AND segment_kind IN ('daytime','overnight')
              AND publication_product = 'still')),
            CONSTRAINT ck_archival_still_active CHECK (active = (retired_at IS NULL))
        )""",
        """CREATE UNIQUE INDEX uq_archival_still_active
           ON weather.archival_still (camera_id, slot_starts_at) WHERE active""",
        """CREATE INDEX ix_archival_still_local_date ON weather.archival_still (local_date)
           WHERE active""",
        """CREATE TABLE weather.still_derivative (
            archival_still_id uuid NOT NULL,
            role varchar(16) NOT NULL,
            asset_id uuid NOT NULL,
            asset_class varchar(16) DEFAULT 'still' NOT NULL,
            recipe varchar(64) NOT NULL,
            state varchar(16) DEFAULT 'published' NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_still_derivative PRIMARY KEY (archival_still_id, role),
            CONSTRAINT fk_still_derivative_archival_still_id_archival_still
              FOREIGN KEY (archival_still_id)
              REFERENCES weather.archival_still (id) ON DELETE RESTRICT,
            -- Only the public still class: a derivative can never reference an original.
            CONSTRAINT fk_still_derivative_asset_id_media_asset FOREIGN KEY (asset_id, asset_class)
              REFERENCES weather.media_asset (id, class) ON DELETE RESTRICT,
            CONSTRAINT ck_still_derivative_role CHECK (role ~ '^still(_[a-z0-9]+)?$'
              AND asset_class = 'still' AND recipe ~ '^[a-z0-9][a-z0-9./_-]{0,63}$'),
            CONSTRAINT ck_still_derivative_state CHECK (state IN ('published','retired'))
        )""",
        "CREATE INDEX ix_still_derivative_asset_id ON weather.still_derivative (asset_id)",
        # Retirement is one-way: a superseded selection never becomes active again.
        """CREATE FUNCTION weather.guard_still_retirement() RETURNS trigger
           LANGUAGE plpgsql SET search_path=pg_catalog AS $$
           BEGIN
             -- Nested: PL/pgSQL resolves every field of a condition, so test the table first.
             IF TG_TABLE_NAME = 'archival_still' THEN
               IF NEW.active AND NOT OLD.active THEN
                 RAISE EXCEPTION 'a retired selection stays retired' USING ERRCODE='55000';
               END IF;
             ELSIF OLD.state = 'retired' AND NEW.state <> 'retired' THEN
               RAISE EXCEPTION 'a retired derivative stays retired' USING ERRCODE='55000';
             END IF;
             RETURN NEW;
           END $$""",
        *(
            f"""CREATE TRIGGER {table}_retirement BEFORE UPDATE ON weather.{table}
                FOR EACH ROW EXECUTE FUNCTION weather.guard_still_retirement()"""
            for table in ("archival_still", "still_derivative")
        ),
        # The active selection per (camera, slot): sanitized images only, never before the
        # capture upper bound (and so its source time) + 630 s; a withdrawn still lists bare.
        # No original, hash, chunk, member or attempt leaves the weather schema.
        f"""CREATE VIEW public_api.stills WITH (security_barrier=true) AS
            SELECT s.camera_id,s.slot_starts_at,s.local_date,h.utc_offset_minutes,h.fold,
              s.revision,s.policy,s.created_at AS selected_at,
              CASE WHEN t.withdrawn THEN 'withdrawn' ELSE s.state END AS state,
              CASE WHEN t.withdrawn THEN 'withdrawn' ELSE s.reason END AS reason,
              s.source_ts,s.capture_upper,s.offset_us,s.cycle_date,s.segment_kind,
              CASE WHEN t.withdrawn THEN ARRAY[]::uuid[] ELSE m.asset_ids END AS asset_ids
            FROM weather.archival_still s
            JOIN weather.hour_slot h ON h.starts_at = s.slot_starts_at
            CROSS JOIN LATERAL (
              SELECT EXISTS (SELECT 1 FROM weather.product_tombstone pt
                WHERE pt.product='still' AND pt.camera_id=s.camera_id
                  AND pt.product_key = {STILL_KEY}) AS withdrawn) t
            CROSS JOIN LATERAL (
              SELECT coalesce(array_agg(d.asset_id ORDER BY d.role), ARRAY[]::uuid[]) AS asset_ids
              FROM weather.still_derivative d JOIN public_api.assets a ON a.id = d.asset_id
              WHERE d.archival_still_id = s.id AND d.state = 'published') m
            WHERE s.active AND (s.state = 'missing' OR (
              s.source_ts + interval '630 seconds' <= clock_timestamp()
              AND s.capture_upper + interval '630 seconds' <= clock_timestamp()))""",
        # The pipeline (worker role) records selections; the API reads only the view.
        "GRANT SELECT,INSERT ON weather.hour_slot,weather.archival_still,"
        "weather.still_derivative TO weather_worker",
        "GRANT UPDATE (active,retired_at) ON weather.archival_still TO weather_worker",
        "GRANT UPDATE (state) ON weather.still_derivative TO weather_worker",
        "GRANT SELECT ON public_api.stills TO weather_api",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
