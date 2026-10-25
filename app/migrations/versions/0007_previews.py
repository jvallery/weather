# SPDX-License-Identifier: MIT
"""Delayed camera previews (#39): one pointer row per camera, generation and variant.

Expand-only and inline DDL; never import application modules here. The bytes are disposable
files under the staging share's `previews/<camera>/`, never Garage or custody. The CHECKs hold
the P14 embargo (`public_not_before >= captured_upper + 630 s`), a non-reversed capture
interval, a publication time inside the half-open lifetime, `expires_at = captured_lower +
3600 s` and a path confined to the row's own camera directory. `public_api.previews` is a
security barrier that admits a row only while the database clock says it is eligible; the API
role reads only the view.
"""

from alembic import op

revision = "0007_previews"
down_revision = "0009_stills"
branch_labels = None
depends_on = None

CAMERAS = "'skycam_east','skycam_west','frontyard_1'"
VISIBLE = """withdrawn_at IS NULL
              AND captured_lower <= captured_upper
              AND captured_upper <= clock_timestamp()
              AND public_not_before >= captured_upper + interval '630 seconds'
              AND public_not_before <= clock_timestamp()
              AND first_published_at <= clock_timestamp()
              AND clock_timestamp() < expires_at"""


def statements() -> list[str]:
    return [
        f"""CREATE TABLE weather.camera_preview (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            camera_id varchar(32) NOT NULL,
            generation bigint NOT NULL,
            variant varchar(8) NOT NULL,
            width integer NOT NULL,
            height integer NOT NULL,
            bytes integer NOT NULL,
            sha256 varchar(64) NOT NULL,
            path varchar(96) NOT NULL,
            captured_lower timestamptz NOT NULL,
            captured_upper timestamptz NOT NULL,
            public_not_before timestamptz NOT NULL,
            first_published_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            withdrawn_at timestamptz,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_camera_preview PRIMARY KEY (id),
            CONSTRAINT uq_camera_preview_camera_id_generation_variant
              UNIQUE (camera_id, generation, variant),
            CONSTRAINT ck_camera_preview_identity CHECK (camera_id IN ({CAMERAS})
              AND generation > 0 AND variant IN ('large','small')),
            CONSTRAINT ck_camera_preview_image CHECK (width BETWEEN 1 AND 1280
              AND height BETWEEN 1 AND 16384 AND bytes BETWEEN 1 AND 2097152
              AND sha256 ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_camera_preview_path CHECK (path = 'previews/' || camera_id || '/'
              || generation || '-' || variant || '.jpg'),
            CONSTRAINT ck_camera_preview_capture CHECK (captured_lower <= captured_upper),
            CONSTRAINT ck_camera_preview_embargo CHECK (
              public_not_before >= captured_upper + interval '630 seconds'),
            CONSTRAINT ck_camera_preview_published CHECK (
              first_published_at >= public_not_before AND first_published_at < expires_at),
            CONSTRAINT ck_camera_preview_expiry CHECK (
              expires_at = captured_lower + interval '3600 seconds')
        )""",
        # The bytes route resolves by id; the pointer takes the camera's newest visible
        # generation through the unique index.
        f"""CREATE VIEW public_api.previews WITH (security_barrier=true) AS
            SELECT id,camera_id,generation,variant,width,height,bytes,sha256,path,
              captured_lower,captured_upper,public_not_before,first_published_at,expires_at
            FROM weather.camera_preview
            WHERE {VISIBLE}""",
        # The pipeline writes and expires rows; nothing rewrites timing or content.
        "GRANT SELECT,INSERT,DELETE ON weather.camera_preview TO weather_worker",
        "GRANT UPDATE (withdrawn_at) ON weather.camera_preview TO weather_worker",
        "GRANT SELECT ON public_api.previews TO weather_api",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
