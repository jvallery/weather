# SPDX-License-Identifier: MIT
"""Catalog thumbnails: `public_api.videos` gains the small poster and the keogram thumbnail.

Expand-only on top of 0014_editorial_poster. Inline DDL; never import application modules here.
The catalog served only the 1920 px poster and the full keogram, so a month calendar or a page
of tiles downloaded megabytes. The pipeline already publishes `poster_480` with every video and
`keogram_thumb` with every keogram. The view is replaced column for column (as 0008 left it)
with two columns appended: `poster_small_asset_id` under the same rules as `poster_asset_id`,
and `keogram_thumb_asset_id` under the same rules as `keogram_asset_id` (null when withdrawn,
before the embargo, or when the keogram itself is missing). Dependent views keep working,
because CREATE OR REPLACE VIEW only appends columns.
"""

from alembic import op

revision = "0015_catalog_thumbnails"
down_revision = "0014_editorial_poster"
branch_labels = None
depends_on = None

VIDEO_ROLES = ("native", "web", "mobile", "poster")


def videos_view() -> str:
    """0008's `public_api.videos`, column for column, with two thumbnail columns appended."""
    roles = ",".join(f"'{role}'" for role in VIDEO_ROLES)
    picks = ",\n".join(
        f"(array_agg(a.id) FILTER (WHERE pa.role='{role}'))[1] AS {role}_asset_id"
        for role in (*VIDEO_ROLES, "sidecar", "poster_480")
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
               ELSE kp.state END AS keogram_state,
          CASE WHEN v.withdrawn OR v.state='unavailable' OR NOT m.complete THEN NULL
               ELSE m.poster_480_asset_id END AS poster_small_asset_id,
          CASE WHEN v.withdrawn OR kt.withdrawn OR NOT kp.visible OR k.asset_id IS NULL THEN NULL
               ELSE k.thumb_id END AS keogram_thumb_asset_id
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
          SELECT (array_agg(a.id) FILTER (WHERE pa.role = 'keogram'))[1] AS asset_id,
            (array_agg(a.id) FILTER (WHERE pa.role = 'keogram_thumb'))[1] AS thumb_id
          FROM weather.publication_asset pa
          JOIN public_api.assets a ON a.id = pa.asset_id
          WHERE pa.publication_id = kp.id AND pa.role IN ('keogram','keogram_thumb')) k ON true
        LEFT JOIN LATERAL (
          SELECT kg.state FROM weather.keogram kg
          JOIN weather.segment s ON s.id = kg.segment_id
          WHERE s.current AND s.camera_id=v.camera_id AND s.cycle_date=v.cycle_date
            AND s.kind=v.segment) ks ON true
        WHERE v.public_not_before <= clock_timestamp()
          AND v.ends_at + interval '630 seconds' <= clock_timestamp()"""


def upgrade() -> None:
    op.execute(videos_view())


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
