# SPDX-License-Identifier: MIT
"""Search documents, embedding profiles and vectors (#25); replaces the removed v1 search table.

Expand-only and inline DDL; never import application modules here. A search document is an
immutable revision of public text about one video: one `video` document (embedded) and any
number of hour-`window` documents (full-text only). A new description, editorial or media
revision withdraws the old documents in the producer's transaction; withdrawal is final, so a
late embedding job can never make old text searchable again.

`video_id` references the publication catalog's `weather.video` (#18, 0003_publication); a
video is never deleted (withdrawal is a flag), so RESTRICT never blocks the catalog.

The API reads two security-barrier views. A vector ranks only through a public document with
the same text hash, and only for the profile the API is configured with. Full-text matching
goes through `public_api.search_lexical`, a SECURITY DEFINER function with the same public
filter inline: `@@` is not leakproof, so through the barrier view it could never use the
partial GIN index and would scan every document revision.
"""

from alembic import op

revision = "0004_search"
down_revision = "0003_publication"
branch_labels = None
depends_on = None

PROFILE = "embed-main-2560-v1"
QUERY_PREFIX = (
    "'Instruct: Given a web search query, retrieve relevant passages that answer the query'"
    " || chr(10) || 'Query:'"
)
# Documents are public at most once: never before their embargo or ends_at + 630 s.
PUBLIC = (
    "d.visibility='public' AND d.public_not_before <= clock_timestamp()"
    " AND d.video_end + interval '630 seconds' <= clock_timestamp()"
)
# Ranked public lexical candidates: ts_rank_cd(…, 1) then document ID, at most 200.
TSQUERY = "pg_catalog.plainto_tsquery('pg_catalog.english', $1)"
LEXICAL_BODY = f"""SELECT d.id,d.video_id FROM weather.search_document d
            WHERE d.lexical @@ {TSQUERY} AND {PUBLIC}
              AND d.source_end > $2 AND d.source_start < $3
              AND (cardinality($4) = 0 OR d.camera_id = ANY($4))
              AND (cardinality($5) = 0 OR d.segment = ANY($5))
              AND (NOT $6 OR d.video_id = ANY($7))
            ORDER BY pg_catalog.ts_rank_cd(d.lexical, {TSQUERY}, 1) DESC, d.id
            LIMIT least($8, 200)"""


def statements() -> list[str]:
    """One PostgreSQL statement per item; function bodies may contain semicolons."""
    return [
        """CREATE TABLE weather.embedding_profile (
            id varchar(64) NOT NULL,
            model varchar(128) NOT NULL,
            dimensions integer NOT NULL,
            query_prefix text NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_embedding_profile PRIMARY KEY (id),
            CONSTRAINT ck_embedding_profile_identity
              CHECK (id ~ '^[a-z0-9][a-z0-9.-]{0,63}$' AND dimensions = 2560)
        )""",
        f"""INSERT INTO weather.embedding_profile (id, model, dimensions, query_prefix)
            VALUES ('{PROFILE}', 'embed.main', 2560, {QUERY_PREFIX})""",
        """CREATE TABLE weather.search_document (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            video_id uuid NOT NULL,
            kind varchar(8) NOT NULL,
            camera_id varchar(32) NOT NULL,
            segment varchar(16) NOT NULL,
            video_start timestamptz NOT NULL,
            video_end timestamptz NOT NULL,
            source_start timestamptz NOT NULL,
            source_end timestamptz NOT NULL,
            media_start_seconds double precision,
            media_end_seconds double precision,
            media_revision integer NOT NULL,
            description_revision integer NOT NULL,
            editorial_revision integer NOT NULL,
            analysis_mode varchar(32) NOT NULL,
            poster_asset_id uuid,
            public_not_before timestamptz NOT NULL,
            text text NOT NULL,
            text_hash varchar(64) NOT NULL,
            lexical tsvector GENERATED ALWAYS AS
              (to_tsvector('pg_catalog.english'::regconfig, text)) STORED,
            visibility varchar(16) DEFAULT 'public' NOT NULL,
            index_status varchar(16) NOT NULL,
            withdrawn_at timestamptz,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_search_document PRIMARY KEY (id),
            CONSTRAINT uq_search_document_id_text_hash UNIQUE (id, text_hash),
            CONSTRAINT fk_search_document_video_id_video FOREIGN KEY (video_id)
              REFERENCES weather.video (id) ON DELETE RESTRICT,
            CONSTRAINT ck_search_document_identity CHECK (kind IN ('video','window')
              AND camera_id ~ '^[a-z][a-z0-9_]{0,31}$' AND segment IN ('daytime','overnight')
              AND analysis_mode ~ '^[a-z][a-z0-9_]{0,31}$' AND media_revision > 0
              AND description_revision >= 0 AND editorial_revision >= 0),
            CONSTRAINT ck_search_document_interval CHECK (video_start < video_end
              AND video_end - video_start <= interval '2 days' AND source_start < source_end
              AND source_start >= video_start AND source_end <= video_end AND (kind = 'window'
                OR (source_start = video_start AND source_end = video_end))),
            CONSTRAINT ck_search_document_seek CHECK (
              (media_start_seconds IS NULL) = (media_end_seconds IS NULL)
              AND (media_start_seconds IS NULL
                OR (media_start_seconds >= 0 AND media_start_seconds < media_end_seconds))),
            CONSTRAINT ck_search_document_embargo
              CHECK (public_not_before >= video_end + interval '630 seconds'),
            CONSTRAINT ck_search_document_text CHECK (length(text) BETWEEN 1 AND 8000
              AND text_hash = encode(pg_catalog.sha256(convert_to(text, 'UTF8')), 'hex')),
            CONSTRAINT ck_search_document_state CHECK (visibility IN ('public','withdrawn')
              AND (visibility = 'withdrawn') = (withdrawn_at IS NOT NULL)
              AND index_status IN ('pending','indexed','lexical')
              AND (kind = 'window') = (index_status = 'lexical'))
        )""",
        # At most one current public document per video and per hour window.
        """CREATE UNIQUE INDEX uq_search_document_video ON weather.search_document (video_id)
           WHERE kind = 'video' AND visibility = 'public'""",
        """CREATE UNIQUE INDEX uq_search_document_window
           ON weather.search_document (video_id, source_start)
           WHERE kind = 'window' AND visibility = 'public'""",
        "CREATE INDEX ix_search_document_video_id ON weather.search_document (video_id)",
        """CREATE INDEX ix_search_document_lexical ON weather.search_document
           USING gin (lexical) WHERE visibility = 'public'""",
        # Withdrawal is final: no update can make withdrawn text current again.
        """CREATE FUNCTION weather.reject_withdrawn_search_change() RETURNS trigger
           LANGUAGE plpgsql SET search_path=pg_catalog AS $$
           BEGIN
             RAISE EXCEPTION 'withdrawn search documents are immutable' USING ERRCODE='55000';
           END $$""",
        """CREATE TRIGGER search_document_withdrawal_final
           BEFORE UPDATE ON weather.search_document FOR EACH ROW
           WHEN (OLD.visibility = 'withdrawn')
           EXECUTE FUNCTION weather.reject_withdrawn_search_change()""",
        """CREATE TABLE weather.search_embedding (
            document_id uuid NOT NULL,
            profile_id varchar(64) NOT NULL,
            text_hash varchar(64) NOT NULL,
            embedding public.vector(2560) NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_search_embedding PRIMARY KEY (document_id, profile_id),
            CONSTRAINT fk_search_embedding_document_id_search_document
              FOREIGN KEY (document_id, text_hash)
              REFERENCES weather.search_document (id, text_hash) ON DELETE RESTRICT,
            CONSTRAINT fk_search_embedding_profile_id_embedding_profile
              FOREIGN KEY (profile_id)
              REFERENCES weather.embedding_profile (id) ON DELETE RESTRICT,
            CONSTRAINT ck_search_embedding_normalized
              CHECK (abs(public.vector_norm(embedding) - 1) <= 0.001)
        )""",
        f"""CREATE VIEW public_api.search_documents WITH (security_barrier=true) AS
            SELECT d.id,d.video_id,d.kind,d.camera_id,d.segment,d.video_start,d.video_end,
              d.source_start,d.source_end,d.media_start_seconds,d.media_end_seconds,
              d.media_revision,d.analysis_mode,d.poster_asset_id,d.text,d.lexical
            FROM weather.search_document d WHERE {PUBLIC}""",
        f"""CREATE VIEW public_api.search_vectors WITH (security_barrier=true) AS
            SELECT e.document_id,e.profile_id,e.embedding
            FROM weather.search_embedding e
            JOIN weather.search_document d ON d.id = e.document_id AND d.text_hash = e.text_hash
            WHERE d.kind = 'video' AND {PUBLIC}""",
        "REVOKE ALL ON weather.embedding_profile,weather.search_document,"
        "weather.search_embedding FROM PUBLIC",
        # Plain English matching only: the query text is a parameter, never operators or SQL.
        # Positional parameters keep the body runnable as a prepared statement (tests EXPLAIN it).
        f"""CREATE FUNCTION public_api.search_lexical(p_query text, p_start timestamptz,
              p_end timestamptz, p_cameras text[], p_segments text[], p_restricted boolean,
              p_videos uuid[], p_limit integer)
            RETURNS TABLE (id uuid, video_id uuid)
            LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
            {LEXICAL_BODY} $$""",
        "REVOKE ALL ON FUNCTION public_api.search_lexical(text,timestamptz,timestamptz,text[],"
        "text[],boolean,uuid[],integer) FROM PUBLIC",
        "GRANT EXECUTE ON FUNCTION public_api.search_lexical(text,timestamptz,timestamptz,text[],"
        "text[],boolean,uuid[],integer) TO weather_api",
        "REVOKE ALL ON public_api.search_documents,public_api.search_vectors FROM PUBLIC",
        # The builder (worker role) inserts documents and vectors; it can only withdraw or mark
        # indexed, never rewrite text. The API reads the two views.
        "GRANT SELECT ON weather.embedding_profile TO weather_worker",
        "GRANT SELECT,INSERT ON weather.search_document,weather.search_embedding"
        " TO weather_worker",
        "GRANT UPDATE (visibility,withdrawn_at,index_status) ON weather.search_document"
        " TO weather_worker",
        "GRANT SELECT ON public_api.search_documents,public_api.search_vectors TO weather_api",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
