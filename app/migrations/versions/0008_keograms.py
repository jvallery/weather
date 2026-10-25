# SPDX-License-Identifier: MIT
"""Keograms (#37): streamed bins, per-segment keogram status and the public keogram view.

Expand-only and inline DDL; never import application modules here. `keogram_bin` holds each
bin's selected capture and column while a segment streams (private; deleted once the PNG is
committed and read back). `keogram` is the per-segment status. The keogram itself publishes
through #18's catalog as product `keogram`; `public_api.keograms` is a security barrier that
shows nothing before `ends_at + 630 s` and no asset or metadata before the publication's
embargo. `public_api.videos` is replaced with the same columns so its `keogram_state` also
reports a failed or unavailable keogram.
"""

from alembic import op

revision = "0008_keograms"
down_revision = "0010_cleanup"
branch_labels = None
depends_on = None

STATES = "'pending','published','partial','unavailable','failed'"
VIDEO_ROLES = ("native", "web", "mobile", "poster")


def videos_view() -> str:
    """0003's `public_api.videos`, column for column, with the keogram status added."""
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
    return f"""CREATE OR REPLACE VIEW public_api.videos WITH (security_barrier=true) AS
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
               WHEN (kp.id IS NULL OR NOT kp.visible) AND ks.state IN ('failed','unavailable')
                 THEN ks.state
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
        LEFT JOIN LATERAL (
          SELECT kg.state FROM weather.keogram kg
          JOIN weather.segment s ON s.id = kg.segment_id
          WHERE s.current AND s.camera_id=v.camera_id AND s.cycle_date=v.cycle_date
            AND s.kind=v.segment) ks ON true
        WHERE v.public_not_before <= clock_timestamp()
          AND v.ends_at + interval '630 seconds' <= clock_timestamp()"""


def statements() -> list[str]:
    asset = (
        "(SELECT a.id FROM weather.publication_asset pa JOIN public_api.assets a"
        " ON a.id = pa.asset_id WHERE pa.publication_id = p.id AND pa.role = '{role}')"
    )
    return [
        f"""CREATE TABLE weather.keogram (
            segment_id uuid NOT NULL,
            state varchar(16) DEFAULT 'pending' NOT NULL,
            reason varchar(64),
            region_version varchar(32) NOT NULL,
            region jsonb NOT NULL,
            attempts integer DEFAULT 0 NOT NULL,
            expected_revision integer,
            observed integer,
            gaps jsonb,
            lineage_sha256 varchar(64),
            capture_upper_bound timestamptz,
            public_not_before timestamptz,
            video_synced boolean DEFAULT false NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_keogram PRIMARY KEY (segment_id),
            CONSTRAINT fk_keogram_segment_id_segment FOREIGN KEY (segment_id)
              REFERENCES weather.segment (id) ON DELETE RESTRICT,
            CONSTRAINT ck_keogram_state CHECK (state IN ({STATES})
              AND (state IN ('pending','published')) = (reason IS NULL)),
            CONSTRAINT ck_keogram_region CHECK (jsonb_typeof(region) = 'object'
              AND octet_length(region::text) <= 4096),
            CONSTRAINT ck_keogram_counters CHECK (attempts >= 0
              AND (expected_revision IS NULL OR expected_revision >= 0)),
            -- A published keogram has every bin; a partial one has at least one.
            CONSTRAINT ck_keogram_result CHECK (
              (state IN ('published','partial')) = (observed IS NOT NULL)
              AND (observed IS NULL) = (gaps IS NULL)
              AND (observed IS NULL) = (lineage_sha256 IS NULL)
              AND (observed IS NULL) = (capture_upper_bound IS NULL)
              AND (state <> 'published' OR observed = 1440)
              AND (state <> 'partial' OR observed BETWEEN 1 AND 1439)
              AND (gaps IS NULL OR jsonb_typeof(gaps) = 'array')
              AND (lineage_sha256 IS NULL OR lineage_sha256 ~ '^[0-9a-f]{{64}}$')),
            -- Nothing public before the newest bin's capture upper bound + 600 s + 30 s skew.
            CONSTRAINT ck_keogram_embargo CHECK (
              (observed IS NULL) = (public_not_before IS NULL)
              AND (public_not_before IS NULL
                   OR public_not_before >= capture_upper_bound + interval '630 seconds'))
        )""",
        """CREATE TABLE weather.keogram_bin (
            segment_id uuid NOT NULL,
            bin smallint NOT NULL,
            distance bigint NOT NULL,
            captured_at timestamptz NOT NULL,
            capture_id varchar(64) COLLATE "C" NOT NULL,
            capture_upper_bound timestamptz NOT NULL,
            uncertainty_ms double precision,
            strip bytea NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_keogram_bin PRIMARY KEY (segment_id, bin),
            CONSTRAINT fk_keogram_bin_segment_id_keogram FOREIGN KEY (segment_id)
              REFERENCES weather.keogram (segment_id) ON DELETE RESTRICT,
            CONSTRAINT ck_keogram_bin_sample CHECK (bin BETWEEN 0 AND 1439 AND distance >= 0
              AND capture_upper_bound >= captured_at
              AND (uncertainty_ms IS NULL OR uncertainty_ms >= 0)
              AND length(capture_id) > 0),
            -- At most 512 RGB pixels: a column, never a frame.
            CONSTRAINT ck_keogram_bin_strip CHECK (octet_length(strip) BETWEEN 3 AND 1536
              AND octet_length(strip) % 3 = 0)
        )""",
        videos_view(),
        f"""CREATE VIEW public_api.keograms WITH (security_barrier=true) AS
            SELECT k.segment_id AS id,s.camera_id,s.cycle_date,s.kind AS segment,s.starts_at,
              s.ends_at,st.state,
              CASE WHEN st.state = 'withdrawn' THEN 'withdrawn'
                   WHEN st.shown THEN p.reason
                   WHEN st.state = 'unavailable' AND p.visible THEN 'media_unavailable'
                   WHEN st.state IN ('failed','unavailable') THEN k.reason END AS reason,
              CASE WHEN st.shown THEN p.revision END AS revision,
              CASE WHEN st.shown THEN p.committed_at END AS committed_at,
              CASE WHEN st.shown THEN p.image END AS image_asset_id,
              CASE WHEN st.shown THEN p.thumbnail END AS thumbnail_asset_id,
              CASE WHEN st.shown THEN p.sidecar END AS sidecar_asset_id,
              CASE WHEN st.shown THEN k.observed END AS observed,
              CASE WHEN st.shown THEN k.gaps END AS gaps,
              CASE WHEN st.shown THEN k.lineage_sha256 END AS lineage_sha256,
              CASE WHEN st.shown THEN k.region_version END AS region_version,
              v.id AS video_id,v.state AS video_state,v.starts_at AS video_starts_at,
              v.ends_at AS video_ends_at,v.frames AS video_frames
            FROM weather.keogram k
            JOIN weather.segment s ON s.id = k.segment_id AND s.current
            CROSS JOIN LATERAL (
              SELECT EXISTS (SELECT 1 FROM weather.product_tombstone t
                WHERE t.product='keogram' AND t.camera_id=s.camera_id
                  AND t.product_key = to_char(s.cycle_date,'YYYY-MM-DD') || '/' || s.kind)
                AS withdrawn) kt
            LEFT JOIN LATERAL (
              SELECT p.id,p.state,p.reason,p.revision,p.committed_at,
                p.public_not_before <= clock_timestamp()
                  AND p.source_end + interval '630 seconds' <= clock_timestamp() AS visible,
                {asset.format(role="keogram")} AS image,
                {asset.format(role="keogram_thumb")} AS thumbnail,
                {asset.format(role="sidecar")} AS sidecar
              FROM weather.publication p
              WHERE p.product='keogram' AND p.camera_id=s.camera_id
                AND p.product_key = to_char(s.cycle_date,'YYYY-MM-DD') || '/' || s.kind
              ORDER BY p.revision DESC LIMIT 1) p ON true
            CROSS JOIN LATERAL (
              SELECT x.state, x.state IN ('published','partial') AS shown FROM (
                SELECT CASE WHEN kt.withdrawn THEN 'withdrawn'
                  WHEN p.visible AND p.state <> 'unavailable' AND p.image IS NOT NULL
                    AND p.thumbnail IS NOT NULL AND p.sidecar IS NOT NULL THEN p.state
                  WHEN p.visible THEN 'unavailable'
                  WHEN k.state IN ('failed','unavailable') THEN k.state
                  ELSE 'pending' END AS state) x) st
            LEFT JOIN public_api.videos v ON v.camera_id = s.camera_id
              AND v.cycle_date = s.cycle_date AND v.segment = s.kind
            WHERE s.ends_at + interval '630 seconds' <= clock_timestamp()""",
        "GRANT SELECT,INSERT ON weather.keogram,weather.keogram_bin TO weather_worker",
        "GRANT UPDATE (state,reason,attempts,expected_revision,observed,gaps,lineage_sha256,"
        "capture_upper_bound,public_not_before,video_synced,updated_at)"
        " ON weather.keogram TO weather_worker",
        # Bins are working state: replaced by a nearer capture, deleted once published.
        "GRANT UPDATE (distance,captured_at,capture_id,capture_upper_bound,uncertainty_ms,strip)"
        " ON weather.keogram_bin TO weather_worker",
        "GRANT DELETE ON weather.keogram_bin TO weather_worker",
        "GRANT SELECT ON public_api.keograms TO weather_api",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
