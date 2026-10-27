# SPDX-License-Identifier: MIT
"""Editor poster selection (#78): a human poster choice and where it has been published.

Expand-only on top of 0013_editorial. Inline DDL; never import application modules here.

An editorial revision may name a poster frame of its video (`poster_frame`, an output-frame index
of the published renditions). The editor only records the choice: the camera's pipeline, which
alone holds the media writer key, extracts that frame from the published web rendition and
publishes the poster roles as revision N+1 of the video (the other roles carried unchanged).
`editorial_poster_applied` records each publication of a chosen frame with the poster's SHA-256
(and the editorial head revision that asked for it). A choice is live while some row for its
frame carries the SHA-256 of the video's current poster; otherwise the pipeline publishes it
again, so a pipeline re-render or any other new video revision never replaces a human poster,
and a later edit that carries the same frame forward needs no new publication.
"""

from alembic import op

revision = "0014_editorial_poster"
down_revision = "0013_editorial"
branch_labels = None
depends_on = None

SHA = "~ '^[0-9a-f]{64}$'"


def statements() -> list[str]:
    return [
        """ALTER TABLE weather.editorial_revision ADD COLUMN poster_frame integer""",
        """ALTER TABLE weather.editorial_revision ADD CONSTRAINT ck_editorial_revision_poster
           CHECK (poster_frame IS NULL OR poster_frame BETWEEN 0 AND 7199)""",
        f"""CREATE TABLE weather.editorial_poster_applied (
            video_id uuid NOT NULL,
            editorial_revision integer NOT NULL,
            poster_frame integer NOT NULL,
            video_revision integer NOT NULL,
            poster_sha256 varchar(64) NOT NULL,
            applied_at timestamptz DEFAULT clock_timestamp() NOT NULL,
            CONSTRAINT pk_editorial_poster_applied
              PRIMARY KEY (video_id, editorial_revision, video_revision),
            CONSTRAINT fk_editorial_poster_applied_video_id_video FOREIGN KEY (video_id)
              REFERENCES weather.video (id) ON DELETE RESTRICT,
            CONSTRAINT ck_editorial_poster_applied_content CHECK (editorial_revision > 0
              AND poster_frame BETWEEN 0 AND 7199 AND video_revision > 0
              AND poster_sha256 {SHA})
        )""",
        """CREATE TRIGGER editorial_poster_applied_append_only
           BEFORE UPDATE OR DELETE ON weather.editorial_poster_applied
           FOR EACH ROW EXECUTE FUNCTION weather.reject_audit_change()""",
        "REVOKE ALL ON weather.editorial_poster_applied FROM PUBLIC",
        # The pipeline reads the head choice and records what it published; it never edits.
        "GRANT SELECT (subject_id,revision,video_id) ON weather.editorial_subject"
        " TO weather_worker",
        "GRANT SELECT (subject_id,revision,poster_frame) ON weather.editorial_revision"
        " TO weather_worker",
        "GRANT SELECT,INSERT ON weather.editorial_poster_applied TO weather_worker",
        # The editor shows whether the live poster is the chosen frame yet.
        "GRANT SELECT ON weather.editorial_poster_applied TO weather_editor",
        "GRANT SELECT (id,sha256) ON weather.media_asset TO weather_editor",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
