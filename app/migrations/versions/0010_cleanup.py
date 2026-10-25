# SPDX-License-Identifier: MIT
"""Staging cleanup, object lifecycle and their audit rows (#19).

Expand-only and inline DDL; never import application modules here.

- `staging_chunk` inventories sealed chunks (identity = manifest sha256 + size). Rows are
  metadata and outlive the file: `present` until the cleanup deletes it (`deleted`) or finds
  it gone without a decision (`missing`).
- `staging_deletion` is the deletion audit for chunks, other incarnations' tmp files and
  render directories: written before the unlink, `unlinked_at` set after it, one row per name.
- `object_event` is the append-only object lifecycle audit: `unavailable` (reconcile found the
  object missing), `expired` (retention tombstone), `gc_deleted` and `purged`.
- `media_asset.state` gains `expired`; `state_changed_at` dates every state change so GC can
  apply its grace period.
- `segment.mezzanine_recipe` names the recipe whose durable blocks `mezzanine_through`
  describes; a renderer of another recipe can neither advance it nor resume below it.
"""

from alembic import op

revision = "0010_cleanup"
down_revision = "0007_previews"
branch_labels = None
depends_on = None

CAMERAS = "'skycam_east','skycam_west','frontyard_1'"
SHA256 = "'^[0-9a-f]{64}$'"
OBJECT_KEY = r"'^[a-z]+/[0-9a-f]{64}\.[a-z0-9]{1,8}$'"
# Deletion targets per class, anchored to the row's own camera directory (#19 review): a
# render row can never name sealed input and a tmp row never a sealed chunk.
CHUNK_NAME = r"'/[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}Z/[A-Za-z0-9_-]{1,120}[.]tar$'"
TMP_NAME = r"'/[0-9]{1,20}_[0-9]{1,20}__[A-Za-z0-9_-]{1,120}[.]tar$'"
RENDER_NAME = (
    r"'/[0-9]{4}-[0-9]{2}-[0-9]{2}_(daytime|overnight)_r[0-9]{1,9}-[A-Za-z0-9._-]{1,80}$'"
)


def statements() -> list[str]:
    return [
        f"""CREATE TABLE weather.staging_chunk (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            camera_id varchar(32) NOT NULL,
            name varchar(192) NOT NULL,
            window_start timestamptz NOT NULL,
            window_end timestamptz NOT NULL,
            newest_capture_at timestamptz NOT NULL,
            frames integer NOT NULL,
            bytes bigint NOT NULL,
            manifest_sha256 varchar(64) NOT NULL,
            cadence_ms integer NOT NULL,
            phase_ms integer NOT NULL,
            width integer NOT NULL,
            height integer NOT NULL,
            state varchar(16) DEFAULT 'present' NOT NULL,
            discovered_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            resolved_at timestamptz,
            CONSTRAINT pk_staging_chunk PRIMARY KEY (id),
            CONSTRAINT uq_staging_chunk_name UNIQUE (name),
            CONSTRAINT ck_staging_chunk_identity CHECK (camera_id IN ({CAMERAS})
              AND name ~ ('^sealed/' || camera_id || {CHUNK_NAME})
              AND window_start < window_end AND newest_capture_at >= window_end
              AND frames >= 0 AND bytes > 0 AND manifest_sha256 ~ {SHA256}
              AND cadence_ms > 0 AND phase_ms >= 0 AND width > 0 AND height > 0),
            CONSTRAINT ck_staging_chunk_state CHECK (state IN ('present','deleted','missing')
              AND (state = 'present') = (resolved_at IS NULL))
        )""",
        """CREATE INDEX ix_staging_chunk_camera_id
           ON weather.staging_chunk (camera_id, window_start) WHERE state = 'present'""",
        f"""CREATE TABLE weather.staging_deletion (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            class varchar(16) NOT NULL,
            camera_id varchar(32) NOT NULL,
            name varchar(192) NOT NULL,
            chunk_id uuid,
            segment_id uuid,
            sha256 varchar(64),
            bytes bigint NOT NULL,
            reason varchar(64) NOT NULL,
            decided_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            unlinked_at timestamptz,
            CONSTRAINT pk_staging_deletion PRIMARY KEY (id),
            CONSTRAINT uq_staging_deletion_name UNIQUE (name),
            CONSTRAINT fk_staging_deletion_chunk_id_staging_chunk FOREIGN KEY (chunk_id)
              REFERENCES weather.staging_chunk (id) ON DELETE RESTRICT,
            CONSTRAINT fk_staging_deletion_segment_id_segment FOREIGN KEY (segment_id)
              REFERENCES weather.segment (id) ON DELETE RESTRICT,
            CONSTRAINT ck_staging_deletion_target CHECK (class IN ('chunk','tmp','render')
              AND camera_id IN ({CAMERAS}) AND bytes >= 0 AND length(reason) > 0
              AND (class = 'chunk') = (chunk_id IS NOT NULL)
              AND (class = 'chunk') = (sha256 IS NOT NULL)
              AND (class = 'render') = (segment_id IS NOT NULL)
              AND (sha256 IS NULL OR sha256 ~ {SHA256})
              AND (unlinked_at IS NULL OR unlinked_at >= decided_at)),
            CONSTRAINT ck_staging_deletion_name CHECK (
              (class = 'chunk' AND name ~ ('^sealed/' || camera_id || {CHUNK_NAME}))
              OR (class = 'tmp' AND name ~ ('^tmp/' || camera_id || {TMP_NAME}))
              OR (class = 'render' AND name ~ ('^render/' || camera_id || {RENDER_NAME})))
        )""",
        """CREATE INDEX ix_staging_deletion_camera_id
           ON weather.staging_deletion (camera_id) WHERE unlinked_at IS NULL""",
        f"""CREATE TABLE weather.object_event (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            action varchar(16) NOT NULL,
            bucket varchar(32) NOT NULL,
            object_key varchar(96) NOT NULL,
            asset_id uuid,
            reason varchar(64) NOT NULL,
            actor varchar(64) NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_object_event PRIMARY KEY (id),
            CONSTRAINT fk_object_event_asset_id_media_asset FOREIGN KEY (asset_id)
              REFERENCES weather.media_asset (id) ON DELETE RESTRICT,
            CONSTRAINT ck_object_event_action CHECK (
              action IN ('unavailable','expired','gc_deleted','purged')
              AND bucket IN ('weather-media','weather-originals')
              AND object_key ~ {OBJECT_KEY} AND length(reason) > 0 AND length(actor) > 0)
        )""",
        "CREATE INDEX ix_object_event_object_key ON weather.object_event (object_key)",
        """CREATE TRIGGER object_event_append_only BEFORE UPDATE OR DELETE ON weather.object_event
           FOR EACH ROW EXECUTE FUNCTION weather.reject_audit_change()""",
        # Widening a CHECK and adding a nullable column are both expand steps.
        "ALTER TABLE weather.media_asset ADD COLUMN state_changed_at timestamptz",
        "ALTER TABLE weather.media_asset DROP CONSTRAINT ck_media_asset_state",
        """ALTER TABLE weather.media_asset ADD CONSTRAINT ck_media_asset_state
           CHECK (state IN ('available','unavailable','expired'))""",
        "GRANT SELECT,INSERT ON weather.staging_chunk,weather.staging_deletion,"
        "weather.object_event TO weather_worker",
        "GRANT UPDATE (state,resolved_at) ON weather.staging_chunk TO weather_worker",
        "GRANT UPDATE (unlinked_at) ON weather.staging_deletion TO weather_worker",
        "GRANT UPDATE (state_changed_at) ON weather.media_asset TO weather_worker",
        # The release point names the recipe whose durable blocks it describes.
        "ALTER TABLE weather.segment ADD COLUMN mezzanine_recipe varchar(80)",
        "GRANT UPDATE (mezzanine_recipe) ON weather.segment TO weather_worker",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
