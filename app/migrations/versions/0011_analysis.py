# SPDX-License-Identifier: MIT
"""AI weather descriptions (#23): analysis heads, immutable revisions and raw responses.

Expand-only and inline DDL; never import application modules here. `analysis` is one head row
per video (state machine and CAS base); `analysis_revision` is append-only and pinned to its
media SHA-256, model, prompt/template/schema hashes and evidence hash. Its summary, claims and
limitations are rendered by server-side templates; no model-written prose is stored there
(C18). `author_kind` says who wrote it, so model output never replaces a human (#44) revision;
the worker cannot set it (its INSERT grant excludes the column, so every worker row is
`model`). `ai_raw_response` keeps private model replies for at most 24 h (a CHECK, not just
the purge) and no public role can read it. The API reads one security-barrier view that shows
a revision only for a public, unwithdrawn video and only while it describes the video's
current media, with its author.
"""

from alembic import op

revision = "0011_analysis"
down_revision = "0010_cleanup"
branch_labels = None
depends_on = None

SHA = "~ '^[0-9a-f]{64}$'"
REASON = "~ '^[a-z][a-z0-9_]{0,31}$'"


def statements() -> list[str]:
    return [
        f"""CREATE TABLE weather.analysis (
            video_id uuid NOT NULL,
            publication_id uuid NOT NULL,
            media_sha256 varchar(64) NOT NULL,
            state varchar(16) NOT NULL,
            reason varchar(32),
            revision integer DEFAULT 0 NOT NULL,
            stale boolean DEFAULT false NOT NULL,
            runs integer DEFAULT 0 NOT NULL,
            calls integer DEFAULT 0 NOT NULL,
            refreshes integer DEFAULT 0 NOT NULL,
            attempt_calls integer DEFAULT 0 NOT NULL,
            pending_since timestamptz,
            attempted_at timestamptz,
            next_attempt_at timestamptz,
            evidence_checked_at timestamptz,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_analysis PRIMARY KEY (video_id),
            CONSTRAINT fk_analysis_video_id_video FOREIGN KEY (video_id)
              REFERENCES weather.video (id) ON DELETE RESTRICT,
            CONSTRAINT fk_analysis_publication_id_publication FOREIGN KEY (publication_id)
              REFERENCES weather.publication (id) ON DELETE RESTRICT,
            CONSTRAINT ck_analysis_identity CHECK (media_sha256 {SHA} AND revision >= 0
              AND runs >= 0 AND calls >= 0 AND refreshes >= 0 AND attempt_calls >= 0),
            CONSTRAINT ck_analysis_state CHECK (
              state IN ('pending','complete','degraded','failed')
              AND (state = 'pending') = (pending_since IS NOT NULL)
              AND (state = 'pending' OR next_attempt_at IS NULL)
              AND (state <> 'failed' OR reason IS NOT NULL)
              AND (state <> 'complete' OR reason IS NULL)
              AND (state NOT IN ('complete','degraded') OR revision > 0)
              AND (reason IS NULL OR reason {REASON}))
        )""",
        """CREATE INDEX ix_analysis_state ON weather.analysis (state, next_attempt_at)""",
        f"""CREATE TABLE weather.analysis_revision (
            id uuid NOT NULL,
            video_id uuid NOT NULL,
            revision integer NOT NULL,
            publication_id uuid NOT NULL,
            media_sha256 varchar(64) NOT NULL,
            state varchar(16) NOT NULL,
            reason varchar(32),
            refresh varchar(16) NOT NULL,
            visual_id uuid NOT NULL,
            model_route varchar(64) NOT NULL,
            model varchar(128) NOT NULL,
            prompt_sha256 varchar(64) NOT NULL,
            schema_sha256 varchar(64) NOT NULL,
            evidence_hash varchar(64) NOT NULL,
            evidence_count integer NOT NULL,
            summary text,
            claims jsonb NOT NULL,
            observations jsonb NOT NULL,
            limitations jsonb NOT NULL,
            coverage jsonb NOT NULL,
            inputs jsonb NOT NULL,
            event_keys jsonb NOT NULL,
            calls integer NOT NULL,
            author_kind varchar(16) DEFAULT 'model' NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_analysis_revision PRIMARY KEY (id),
            CONSTRAINT uq_analysis_revision_video_id_revision UNIQUE (video_id, revision),
            CONSTRAINT fk_analysis_revision_video_id_video FOREIGN KEY (video_id)
              REFERENCES weather.video (id) ON DELETE RESTRICT,
            CONSTRAINT fk_analysis_revision_publication_id_publication
              FOREIGN KEY (publication_id) REFERENCES weather.publication (id) ON DELETE RESTRICT,
            CONSTRAINT ck_analysis_revision_identity CHECK (revision > 0 AND calls >= 0
              AND evidence_count >= 0 AND media_sha256 {SHA} AND prompt_sha256 {SHA}
              AND schema_sha256 {SHA} AND evidence_hash {SHA}
              AND author_kind IN ('model','operator')
              AND (author_kind <> 'model' OR (model_route = 'llm.primary.longctx.mm'
                AND model ~ '^[A-Za-z0-9][A-Za-z0-9._:/+-]{{0,127}}$'))),
            CONSTRAINT ck_analysis_revision_state CHECK (state IN ('complete','degraded')
              AND refresh IN ('full','measurements')
              AND (state = 'complete') = (reason IS NULL)
              AND (reason IS NULL OR reason {REASON})),
            CONSTRAINT ck_analysis_revision_content CHECK (
              (summary IS NULL OR length(summary) BETWEEN 1 AND 8000)
              AND jsonb_typeof(claims) = 'array' AND jsonb_array_length(claims) <= 16
              AND jsonb_typeof(observations) = 'array'
              AND jsonb_typeof(limitations) = 'array' AND jsonb_array_length(limitations) <= 8
              AND jsonb_typeof(coverage) = 'object' AND jsonb_typeof(inputs) = 'object'
              AND jsonb_typeof(event_keys) = 'array'
              AND octet_length(observations::text) <= 1048576)
        )""",
        # Revisions are immutable history; reuse #18's append-only guard.
        """CREATE TRIGGER analysis_revision_append_only
           BEFORE UPDATE OR DELETE ON weather.analysis_revision
           FOR EACH ROW EXECUTE FUNCTION weather.reject_audit_change()""",
        """CREATE TABLE weather.ai_raw_response (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            video_id uuid NOT NULL,
            stage varchar(16) NOT NULL,
            window_start timestamptz,
            call integer NOT NULL,
            outcome varchar(32) NOT NULL,
            finish_reason varchar(32),
            body text,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            expires_at timestamptz DEFAULT (CURRENT_TIMESTAMP + interval '24 hours') NOT NULL,
            CONSTRAINT pk_ai_raw_response PRIMARY KEY (id),
            CONSTRAINT fk_ai_raw_response_video_id_video FOREIGN KEY (video_id)
              REFERENCES weather.video (id) ON DELETE RESTRICT,
            CONSTRAINT ck_ai_raw_response_identity CHECK (stage = 'visual'
              AND call > 0 AND outcome ~ '^[a-z][a-z0-9_]{0,31}$'),
            -- Private diagnostics live at most a day, whatever the purge does.
            CONSTRAINT ck_ai_raw_response_retention
              CHECK (expires_at > created_at AND expires_at <= created_at + interval '24 hours'),
            CONSTRAINT ck_ai_raw_response_body
              CHECK (body IS NULL OR octet_length(body) <= 262144)
        )""",
        "CREATE INDEX ix_ai_raw_response_expires_at ON weather.ai_raw_response (expires_at)",
        """CREATE VIEW public_api.video_analyses WITH (security_barrier=true) AS
            SELECT a.video_id,a.state,a.reason,a.stale,a.updated_at,r.revision,
              r.state AS revision_state,r.refresh,r.model_route,r.model,r.prompt_sha256,
              r.schema_sha256,r.evidence_count,r.summary,r.claims,r.limitations,r.coverage,
              r.inputs,r.author_kind,r.created_at AS generated_at
            FROM weather.analysis a
            JOIN public_api.videos v ON v.id = a.video_id AND NOT v.withdrawn
            LEFT JOIN weather.analysis_revision r ON r.video_id = a.video_id
              AND r.revision = a.revision AND r.publication_id = a.publication_id
              AND r.media_sha256 = a.media_sha256""",
        "REVOKE ALL ON weather.analysis,weather.analysis_revision,weather.ai_raw_response"
        " FROM PUBLIC",
        "REVOKE ALL ON public_api.video_analyses FROM PUBLIC",
        "GRANT SELECT,INSERT ON weather.analysis TO weather_worker",
        "GRANT SELECT ON weather.analysis_revision TO weather_worker",
        # Every column but author_kind: worker rows are always the model's.
        "GRANT INSERT (id,video_id,revision,publication_id,media_sha256,state,reason,refresh,"
        "visual_id,model_route,model,prompt_sha256,schema_sha256,evidence_hash,evidence_count,"
        "summary,claims,observations,limitations,coverage,inputs,event_keys,calls)"
        " ON weather.analysis_revision TO weather_worker",
        "GRANT UPDATE (publication_id,media_sha256,state,reason,revision,stale,runs,calls,"
        "refreshes,attempt_calls,pending_since,attempted_at,next_attempt_at,evidence_checked_at,"
        "updated_at)"
        " ON weather.analysis TO weather_worker",
        "GRANT SELECT,INSERT,DELETE ON weather.ai_raw_response TO weather_worker",
        # Neither the raw table nor the revision tables: only the filtered view.
        "GRANT SELECT ON public_api.video_analyses TO weather_api",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
