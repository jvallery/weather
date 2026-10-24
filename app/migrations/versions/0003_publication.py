# SPDX-License-Identifier: MIT
"""Publication catalog (#18): upload intents, content-addressed assets, products, withdrawals.

Expand-only and inline DDL; never import application modules here. The privacy CHECKs come
from the removed slice-2 DDL (fc34b83): originals are private and only in the originals bucket,
object keys are content addresses, and nothing public lacks an embargo. The public views are
security barriers, and a video is never visible before `ends_at + 630 s`.
"""

from alembic import op

revision = "0003_publication"
down_revision = "0002_api"
branch_labels = None
depends_on = None

PRODUCT = "product IN ('video','keogram','still')"
CAMERA = "camera_id ~ '^[a-z][a-z0-9_]{0,31}$'"
PRODUCT_KEY = "product_key ~ '^[0-9A-Za-z][0-9A-Za-z:/_.-]{0,63}$'"
CLASSES = "'video','poster','keogram','still','sidecar'"
WITHDRAWAL = "withdrawn = (withdrawn_at IS NOT NULL) AND withdrawn = (withdraw_reason IS NOT NULL)"
VIDEO_ROLES = ("native", "web", "mobile", "poster")


def statements() -> list[str]:
    """One PostgreSQL statement per item; function bodies may contain semicolons."""
    roles = ",".join(f"'{role}'" for role in VIDEO_ROLES)
    picks = ",\n".join(
        f"(array_agg(a.id) FILTER (WHERE pa.role='{role}'))[1] AS {role}_asset_id"
        for role in (*VIDEO_ROLES, "sidecar")
    )
    media = ",\n".join(
        f"CASE WHEN v.withdrawn OR v.state='unavailable' OR NOT m.complete THEN NULL "
        f"ELSE m.{role}_asset_id END AS {role}_asset_id"
        for role in VIDEO_ROLES
    )
    return [
        # Every publish attempt takes a new token; a commit from an older attempt is stale.
        "CREATE SEQUENCE weather.publication_fence AS bigint",
        f"""CREATE TABLE weather.media_asset (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            class varchar(16) NOT NULL,
            bucket varchar(32) NOT NULL,
            visibility varchar(8) NOT NULL,
            sha256 varchar(64) NOT NULL,
            extension varchar(8) NOT NULL,
            object_key varchar(96) NOT NULL,
            bytes bigint NOT NULL,
            content_type varchar(64) NOT NULL,
            width integer,
            height integer,
            public_not_before timestamptz,
            published_at timestamptz,
            sidecar bytea,
            state varchar(16) DEFAULT 'available' NOT NULL,
            withdrawn boolean DEFAULT false NOT NULL,
            withdrawn_at timestamptz,
            withdraw_reason varchar(500),
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_media_asset PRIMARY KEY (id),
            CONSTRAINT uq_media_asset_object_key UNIQUE (object_key),
            CONSTRAINT uq_media_asset_id_class UNIQUE (id, class),
            CONSTRAINT ck_media_asset_class CHECK (class IN ({CLASSES},'original')),
            CONSTRAINT ck_media_asset_original_private CHECK (
              bucket IN ('weather-media','weather-originals')
              AND visibility IN ('public','private')
              AND (class = 'original') = (bucket = 'weather-originals')
              AND (class = 'original') = (visibility = 'private')),
            CONSTRAINT ck_media_asset_embargo CHECK (
              (class = 'original') = (public_not_before IS NULL)
              AND (class = 'original') = (published_at IS NULL)),
            CONSTRAINT ck_media_asset_content_address CHECK (
              sha256 ~ '^[0-9a-f]{{64}}$' AND extension ~ '^[a-z0-9]{{1,8}}$' AND bytes > 0
              AND object_key = class || '/' || sha256 || '.' || extension),
            CONSTRAINT ck_media_asset_geometry CHECK ((width IS NULL) = (height IS NULL)
              AND (width IS NULL OR (width > 0 AND height > 0))),
            CONSTRAINT ck_media_asset_sidecar CHECK ((class = 'sidecar') = (sidecar IS NOT NULL)
              AND (sidecar IS NULL OR (octet_length(sidecar) = bytes AND bytes <= 1048576
                AND encode(pg_catalog.sha256(sidecar),'hex') = sha256))),
            CONSTRAINT ck_media_asset_state CHECK (state IN ('available','unavailable')),
            CONSTRAINT ck_media_asset_withdrawal CHECK ({WITHDRAWAL})
        )""",
        f"""CREATE TABLE weather.upload_intent (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            fence bigint NOT NULL,
            product varchar(16) NOT NULL,
            camera_id varchar(32) NOT NULL,
            product_key varchar(64) NOT NULL,
            expected_revision integer NOT NULL,
            manifest jsonb NOT NULL,
            state varchar(16) DEFAULT 'pending' NOT NULL,
            reason varchar(32),
            deadline timestamptz NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            resolved_at timestamptz,
            CONSTRAINT pk_upload_intent PRIMARY KEY (id),
            CONSTRAINT uq_upload_intent_fence UNIQUE (fence),
            CONSTRAINT ck_upload_intent_identity CHECK ({PRODUCT} AND {CAMERA} AND {PRODUCT_KEY}
              AND fence > 0 AND expected_revision >= 0),
            CONSTRAINT ck_upload_intent_state CHECK (state IN ('pending','committed','orphan')
              AND (state = 'pending') = (resolved_at IS NULL)
              AND (state = 'orphan') = (reason IS NOT NULL)),
            CONSTRAINT ck_upload_intent_manifest CHECK (jsonb_typeof(manifest) = 'object'
              AND octet_length(manifest::text) <= 1048576),
            -- An intent lives at most two days, far inside the 90-day GC grace (#19).
            CONSTRAINT ck_upload_intent_deadline CHECK (deadline <= created_at + interval '2 days')
        )""",
        """CREATE INDEX ix_upload_intent_product
           ON weather.upload_intent (product, camera_id, product_key, fence)""",
        """CREATE INDEX ix_upload_intent_created_at ON weather.upload_intent (created_at)
           WHERE state = 'pending'""",
        # A retry can never move the intent's original commit deadline.
        """CREATE TRIGGER upload_intent_deadline_immutable
           BEFORE UPDATE OF deadline ON weather.upload_intent
           FOR EACH ROW EXECUTE FUNCTION weather.reject_deadline_change()""",
        # One row per committed revision of any product; the revision log is append only.
        f"""CREATE TABLE weather.publication (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            product varchar(16) NOT NULL,
            camera_id varchar(32) NOT NULL,
            product_key varchar(64) NOT NULL,
            revision integer NOT NULL,
            fence bigint NOT NULL,
            intent_id uuid NOT NULL,
            state varchar(16) NOT NULL,
            reason varchar(64),
            source_start timestamptz NOT NULL,
            source_end timestamptz NOT NULL,
            public_not_before timestamptz NOT NULL,
            committed_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_publication PRIMARY KEY (id),
            CONSTRAINT uq_publication_product_camera_id_product_key_revision
              UNIQUE (product, camera_id, product_key, revision),
            CONSTRAINT uq_publication_id_product UNIQUE (id, product),
            CONSTRAINT fk_publication_intent_id_upload_intent FOREIGN KEY (intent_id)
              REFERENCES weather.upload_intent (id) ON DELETE RESTRICT,
            CONSTRAINT ck_publication_identity CHECK ({PRODUCT} AND {CAMERA} AND {PRODUCT_KEY}
              AND revision > 0 AND fence > 0),
            CONSTRAINT ck_publication_state CHECK (state IN ('published','partial','unavailable')
              AND (state = 'published' OR reason IS NOT NULL)),
            CONSTRAINT ck_publication_embargo CHECK (source_start < source_end
              AND public_not_before >= source_end + interval '630 seconds')
        )""",
        "CREATE INDEX ix_publication_intent_id ON weather.publication (intent_id)",
        """CREATE TABLE weather.publication_asset (
            publication_id uuid NOT NULL,
            product varchar(16) NOT NULL,
            role varchar(16) NOT NULL,
            asset_id uuid NOT NULL,
            asset_class varchar(16) NOT NULL,
            CONSTRAINT pk_publication_asset PRIMARY KEY (publication_id, role),
            CONSTRAINT fk_publication_asset_publication_id_publication
              FOREIGN KEY (publication_id, product)
              REFERENCES weather.publication (id, product) ON DELETE RESTRICT,
            CONSTRAINT fk_publication_asset_asset_id_media_asset
              FOREIGN KEY (asset_id, asset_class)
              REFERENCES weather.media_asset (id, class) ON DELETE RESTRICT,
            CONSTRAINT ck_publication_asset_role CHECK (role ~ '^[a-z]+(_[a-z0-9]+)?$'),
            -- Originals commit only to the private still catalog, in their own role.
            CONSTRAINT ck_publication_asset_original_private CHECK (
              (asset_class = 'original') = (role = 'original')
              AND (asset_class <> 'original' OR product = 'still'))
        )""",
        "CREATE INDEX ix_publication_asset_asset_id ON weather.publication_asset (asset_id)",
        # The current state of each (camera, solar cycle, segment) video; one row, ever.
        f"""CREATE TABLE weather.video (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            camera_id varchar(32) NOT NULL,
            cycle_date date NOT NULL,
            segment varchar(16) NOT NULL,
            publication_id uuid NOT NULL,
            revision integer NOT NULL,
            fence bigint NOT NULL,
            state varchar(16) NOT NULL,
            reason varchar(64),
            starts_at timestamptz NOT NULL,
            ends_at timestamptz NOT NULL,
            frames integer NOT NULL,
            public_not_before timestamptz NOT NULL,
            committed_at timestamptz NOT NULL,
            withdrawn boolean DEFAULT false NOT NULL,
            withdrawn_at timestamptz,
            withdraw_reason varchar(500),
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_video PRIMARY KEY (id),
            CONSTRAINT uq_video_camera_id_cycle_date_segment
              UNIQUE (camera_id, cycle_date, segment),
            CONSTRAINT fk_video_publication_id_publication FOREIGN KEY (publication_id)
              REFERENCES weather.publication (id) ON DELETE RESTRICT,
            CONSTRAINT ck_video_identity CHECK ({CAMERA}
              AND segment IN ('daytime','overnight') AND revision > 0 AND fence > 0
              AND frames BETWEEN 0 AND 7200 AND starts_at < ends_at),
            CONSTRAINT ck_video_state CHECK (state IN ('published','partial','unavailable')
              AND (state = 'published' OR reason IS NOT NULL)),
            CONSTRAINT ck_video_embargo
              CHECK (public_not_before >= ends_at + interval '630 seconds'),
            CONSTRAINT ck_video_withdrawal CHECK ({WITHDRAWAL})
        )""",
        """CREATE TABLE weather.withdrawal (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            video_id uuid,
            asset_id uuid,
            reason varchar(500) NOT NULL,
            actor varchar(64) NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_withdrawal PRIMARY KEY (id),
            CONSTRAINT fk_withdrawal_video_id_video FOREIGN KEY (video_id)
              REFERENCES weather.video (id) ON DELETE RESTRICT,
            CONSTRAINT fk_withdrawal_asset_id_media_asset FOREIGN KEY (asset_id)
              REFERENCES weather.media_asset (id) ON DELETE RESTRICT,
            CONSTRAINT ck_withdrawal_target CHECK ((video_id IS NULL) <> (asset_id IS NULL)
              AND length(reason) > 0 AND length(actor) > 0)
        )""",
        # A withdrawn product stays withdrawn: every later commit of it is refused, whatever
        # bytes a re-render produces.
        f"""CREATE TABLE weather.product_tombstone (
            product varchar(16) NOT NULL,
            camera_id varchar(32) NOT NULL,
            product_key varchar(64) NOT NULL,
            withdrawal_id uuid NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_product_tombstone PRIMARY KEY (product, camera_id, product_key),
            CONSTRAINT fk_product_tombstone_withdrawal_id_withdrawal FOREIGN KEY (withdrawal_id)
              REFERENCES weather.withdrawal (id) ON DELETE RESTRICT,
            CONSTRAINT ck_product_tombstone_identity
              CHECK ({PRODUCT} AND {CAMERA} AND {PRODUCT_KEY})
        )""",
        """CREATE FUNCTION weather.reject_audit_change() RETURNS trigger
           LANGUAGE plpgsql SET search_path=pg_catalog AS $$
           BEGIN
             RAISE EXCEPTION 'publication audit rows are append only' USING ERRCODE='55000';
           END $$""",
        *(
            f"""CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON weather.{table}
                FOR EACH ROW EXECUTE FUNCTION weather.reject_audit_change()"""
            for table in ("publication", "publication_asset", "withdrawal", "product_tombstone")
        ),
        # Withdrawal is one-way and an asset's embargo never moves; a video's may only move
        # later (a corrected, later segment end needs a later embargo, never an earlier one).
        """CREATE FUNCTION weather.guard_publication_state() RETURNS trigger
           LANGUAGE plpgsql SET search_path=pg_catalog AS $$
           BEGIN
             IF OLD.withdrawn AND NOT NEW.withdrawn THEN
               RAISE EXCEPTION 'withdrawal is permanent' USING ERRCODE='55000';
             END IF;
             IF TG_TABLE_NAME = 'media_asset'
                AND NEW.public_not_before IS DISTINCT FROM OLD.public_not_before THEN
               RAISE EXCEPTION 'asset embargo is fixed' USING ERRCODE='55000';
             END IF;
             IF TG_TABLE_NAME = 'video' AND NEW.public_not_before < OLD.public_not_before THEN
               RAISE EXCEPTION 'video embargo never moves earlier' USING ERRCODE='55000';
             END IF;
             RETURN NEW;
           END $$""",
        *(
            f"""CREATE TRIGGER {table}_guard BEFORE UPDATE ON weather.{table}
                FOR EACH ROW EXECUTE FUNCTION weather.guard_publication_state()"""
            for table in ("media_asset", "video")
        ),
        # Public assets: never originals, never withdrawn or unavailable, never before embargo.
        f"""CREATE VIEW public_api.assets WITH (security_barrier=true) AS
            SELECT id,class,sha256,extension,bytes,content_type,width,height,public_not_before
            FROM weather.media_asset
            WHERE class IN ({CLASSES}) AND bucket='weather-media' AND visibility='public'
              AND state='available' AND NOT withdrawn AND withdrawn_at IS NULL
              AND public_not_before <= clock_timestamp()
              AND published_at <= clock_timestamp()""",
        # A video exposes renditions only when all four are public; a withdrawn video lists
        # with no media at all. Nothing shows before its embargo or ends_at + 630 s.
        f"""CREATE VIEW public_api.videos WITH (security_barrier=true) AS
            SELECT v.id,v.camera_id,v.cycle_date,v.segment,v.starts_at,v.ends_at,v.frames,
              v.revision,v.committed_at,v.withdrawn,
              CASE WHEN v.withdrawn THEN 'withdrawn'
                   WHEN v.state <> 'unavailable' AND NOT m.complete THEN 'unavailable'
                   ELSE v.state END AS state,
              CASE WHEN v.withdrawn THEN 'withdrawn'
                   WHEN v.state <> 'unavailable' AND NOT m.complete THEN 'media_unavailable'
                   ELSE v.reason END AS reason,
              {media},
              CASE WHEN v.withdrawn THEN NULL ELSE m.sidecar_asset_id END AS sidecar_asset_id,
              CASE WHEN v.withdrawn OR kt.withdrawn OR NOT kp.visible THEN NULL
                   ELSE k.asset_id END AS keogram_asset_id,
              CASE WHEN v.withdrawn OR kt.withdrawn THEN 'withdrawn'
                   WHEN kp.id IS NULL OR NOT kp.visible THEN 'pending'
                   WHEN k.asset_id IS NULL OR kp.state = 'unavailable' THEN 'unavailable'
                   ELSE kp.state END AS keogram_state
            FROM weather.video v
            CROSS JOIN LATERAL (
              SELECT {picks},
                count(*) FILTER (WHERE pa.role IN ({roles})) = {len(VIDEO_ROLES)} AS complete
              FROM weather.publication_asset pa
              JOIN public_api.assets a ON a.id = pa.asset_id
              WHERE pa.publication_id = v.publication_id) m
            CROSS JOIN LATERAL (
              SELECT EXISTS (SELECT 1 FROM weather.product_tombstone t
                WHERE t.product='keogram' AND t.camera_id=v.camera_id
                  AND t.product_key = to_char(v.cycle_date,'YYYY-MM-DD') || '/' || v.segment)
                AS withdrawn) kt
            LEFT JOIN LATERAL (
              SELECT p.id,p.state,p.public_not_before <= clock_timestamp()
                  AND p.source_end + interval '630 seconds' <= clock_timestamp() AS visible
              FROM weather.publication p
              WHERE p.product='keogram' AND p.camera_id=v.camera_id
                AND p.product_key = to_char(v.cycle_date,'YYYY-MM-DD') || '/' || v.segment
              ORDER BY p.revision DESC LIMIT 1) kp ON true
            LEFT JOIN LATERAL (
              SELECT a.id AS asset_id FROM weather.publication_asset pa
              JOIN public_api.assets a ON a.id = pa.asset_id
              WHERE pa.publication_id = kp.id AND pa.role = 'keogram') k ON true
            WHERE v.public_not_before <= clock_timestamp()
              AND v.ends_at + interval '630 seconds' <= clock_timestamp()""",
        # The pipeline (worker role) writes the catalog; the API reads only the two views.
        "GRANT USAGE ON SEQUENCE weather.publication_fence TO weather_worker",
        # UPDATE only on the columns the code writes; identity, content and audit stay fixed.
        "GRANT SELECT,INSERT ON weather.media_asset,weather.upload_intent,weather.video,"
        "weather.publication,weather.publication_asset,weather.withdrawal,"
        "weather.product_tombstone TO weather_worker",
        "GRANT UPDATE (state,withdrawn,withdrawn_at,withdraw_reason) ON weather.media_asset"
        " TO weather_worker",
        "GRANT UPDATE (state,reason,resolved_at) ON weather.upload_intent TO weather_worker",
        "GRANT UPDATE (publication_id,revision,fence,state,reason,starts_at,ends_at,frames,"
        "public_not_before,committed_at,withdrawn,withdrawn_at,withdraw_reason)"
        " ON weather.video TO weather_worker",
        "GRANT SELECT ON public_api.assets,public_api.videos TO weather_api",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
