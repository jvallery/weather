# SPDX-License-Identifier: MIT
"""The private editor (#44): operator allowlist, editorial heads, revisions and audit.

Expand-only on top of 0012_analysis. Inline DDL; never import application modules here.

`editorial_subject` is one head per edited video or event (the CAS base). Every edit appends an
immutable `editorial_revision` holding the subject's whole editorial state, and an immutable
`editorial_audit` row, in one transaction with the outbox entry. A human description is bound
to the web rendition SHA-256 and the analysis evidence hash it was written against; it is
public only while both still match (`weather.video_editorial_state`), so a media or evidence
change withdraws it without deleting it, and an explicit audited rebase re-binds it.

Favorites, private tags and notes live only in base tables no public role can read; the one
public view exposes the active human description, the public revision (which moves only when
public content changes) and public tags.

Only `weather_editor` may append an `operator` event revision (a trigger), so the editor can
trust that author. Receipts gain a database-assigned `inserted_at` for the private receipts
export cursor (older rows read `-infinity`, no table rewrite), and search document text may
reach 9,000 characters so an 8,000-character description is indexed whole, never truncated.
"""

from alembic import op

revision = "0013_editorial"
down_revision = "0012_analysis"
branch_labels = None
depends_on = None

SHA = "~ '^[0-9a-f]{64}$'"
TAG = "[a-z0-9][a-z0-9 _-]{0,47}"
# The single allowlisted operator: the Authentik username, admitted only through the
# weather-editor application (agents#5366). Change it with a new revision, never by hand.
OPERATOR = ("jason", "weather-editor")


def _tags(column: str) -> str:
    # A CHECK cannot hold a subquery; tags never contain a comma, so match the joined list.
    return (
        f"cardinality({column}) <= 32 AND array_position({column}, NULL) IS NULL"
        f" AND array_to_string({column}, ',') ~ '^$|^{TAG}(,{TAG})*$'"
    )


def statements() -> list[str]:
    return [
        """CREATE TABLE weather.editorial_operator (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            username varchar(256) NOT NULL,
            application varchar(64) NOT NULL,
            active boolean DEFAULT true NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_editorial_operator PRIMARY KEY (id),
            CONSTRAINT uq_editorial_operator_username UNIQUE (username),
            CONSTRAINT ck_editorial_operator_identity CHECK (
              length(username) BETWEEN 1 AND 256 AND application ~ '^[a-z][a-z0-9-]{0,63}$')
        )""",
        f"""INSERT INTO weather.editorial_operator (username, application)
            VALUES ('{OPERATOR[0]}', '{OPERATOR[1]}')""",
        """CREATE TABLE weather.editorial_subject (
            subject_id uuid NOT NULL,
            kind varchar(8) NOT NULL,
            video_id uuid,
            event_id uuid,
            revision integer NOT NULL,
            updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_editorial_subject PRIMARY KEY (subject_id),
            CONSTRAINT fk_editorial_subject_video_id_video FOREIGN KEY (video_id)
              REFERENCES weather.video (id) ON DELETE RESTRICT,
            CONSTRAINT fk_editorial_subject_event_id_weather_event FOREIGN KEY (event_id)
              REFERENCES weather.weather_event (id) ON DELETE RESTRICT,
            CONSTRAINT ck_editorial_subject_kind CHECK (revision > 0 AND (
              (kind = 'video' AND video_id = subject_id AND event_id IS NULL)
              OR (kind = 'event' AND event_id = subject_id AND video_id IS NULL)))
        )""",
        f"""CREATE TABLE weather.editorial_revision (
            subject_id uuid NOT NULL,
            revision integer NOT NULL,
            -- Moves only when public content (description, binding, public tags, an event
            -- change) changes, so a favorite, note or private tag leaves no public trace.
            public_revision integer NOT NULL,
            recorded_at timestamptz DEFAULT clock_timestamp() NOT NULL,
            description text,
            media_sha256 varchar(64),
            evidence_hash varchar(64),
            public_tags varchar(48)[] DEFAULT '{{}}'::varchar[] NOT NULL,
            private_tags varchar(48)[] DEFAULT '{{}}'::varchar[] NOT NULL,
            favorite boolean DEFAULT false NOT NULL,
            note text,
            event_revision integer,
            CONSTRAINT pk_editorial_revision PRIMARY KEY (subject_id, revision),
            CONSTRAINT fk_editorial_revision_subject_id_editorial_subject FOREIGN KEY (subject_id)
              REFERENCES weather.editorial_subject (subject_id) ON DELETE RESTRICT,
            CONSTRAINT ck_editorial_revision_content CHECK (revision > 0
              AND public_revision BETWEEN 0 AND revision
              AND (description IS NULL OR length(description) BETWEEN 1 AND 8000)
              AND (note IS NULL OR length(note) BETWEEN 1 AND 4000)
              AND (event_revision IS NULL OR event_revision > 0)),
            -- A human description is always bound to the media it was written against.
            CONSTRAINT ck_editorial_revision_binding CHECK (
              (description IS NULL OR media_sha256 IS NOT NULL)
              AND (media_sha256 IS NULL OR media_sha256 {SHA})
              AND (evidence_hash IS NULL OR evidence_hash {SHA})),
            CONSTRAINT ck_editorial_revision_tags CHECK ({_tags("public_tags")}
              AND {_tags("private_tags")} AND NOT (public_tags && private_tags))
        )""",
        """CREATE TABLE weather.editorial_audit (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            subject_id uuid NOT NULL,
            action varchar(16) NOT NULL,
            actor varchar(256) NOT NULL,
            recorded_at timestamptz DEFAULT clock_timestamp() NOT NULL,
            reason text NOT NULL,
            old_revision integer NOT NULL,
            new_revision integer NOT NULL,
            input jsonb NOT NULL,
            media_sha256 varchar(64),
            evidence_hash varchar(64),
            evidence_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
            event_revision integer,
            CONSTRAINT pk_editorial_audit PRIMARY KEY (id),
            CONSTRAINT fk_editorial_audit_subject_id_editorial_revision
              FOREIGN KEY (subject_id, new_revision)
              REFERENCES weather.editorial_revision (subject_id, revision) ON DELETE RESTRICT,
            CONSTRAINT ck_editorial_audit_content CHECK (
              action IN ('edit','rebase','manual_event')
              AND length(actor) BETWEEN 1 AND 256 AND length(reason) BETWEEN 1 AND 2000
              AND old_revision >= 0 AND new_revision = old_revision + 1
              AND jsonb_typeof(input) = 'object' AND octet_length(input::text) <= 65536
              AND cardinality(evidence_ids) <= 256)
        )""",
        """CREATE INDEX ix_editorial_audit_subject_id
           ON weather.editorial_audit (subject_id, recorded_at)""",
        # Revisions and audit are immutable history; reuse #18's append-only guard.
        *(
            f"""CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON weather.{table}
                FOR EACH ROW EXECUTE FUNCTION weather.reject_audit_change()"""
            for table in ("editorial_revision", "editorial_audit")
        ),
        # Only the editor writes operator event revisions: weather_worker may append events,
        # but an operator author it wrote would be trusted by the editor as a human decision.
        """CREATE FUNCTION weather.guard_operator_event_revision() RETURNS trigger
           LANGUAGE plpgsql SET search_path=pg_catalog AS $$
           BEGIN
             IF NEW.author_kind = 'operator' AND current_user <> 'weather_editor' THEN
               RAISE EXCEPTION 'operator event revisions are written by the editor only'
                 USING ERRCODE='42501';
             END IF;
             RETURN NEW;
           END $$""",
        """CREATE TRIGGER weather_event_revision_operator
           BEFORE INSERT ON weather.weather_event_revision
           FOR EACH ROW EXECUTE FUNCTION weather.guard_operator_event_revision()""",
        # Private receipts export (#44): rows that land later (spool drain) never shift a page.
        """ALTER TABLE weather.telemetry_receipt
           ADD COLUMN inserted_at timestamptz DEFAULT '-infinity' NOT NULL""",
        """ALTER TABLE weather.telemetry_receipt
           ALTER COLUMN inserted_at SET DEFAULT clock_timestamp()""",
        """ALTER TABLE weather.search_document DROP CONSTRAINT ck_search_document_text""",
        """ALTER TABLE weather.search_document ADD CONSTRAINT ck_search_document_text
           CHECK (length(text) BETWEEN 1 AND 9000
             AND text_hash = encode(pg_catalog.sha256(convert_to(text, 'UTF8')), 'hex'))""",
        # The one definition of a video's effective editorial state, read by the API (through
        # the public view below), the editor and the AI/search projection. The automated text
        # is eligible only for the video's current web rendition, as in public_api.video_analyses.
        """CREATE VIEW weather.video_editorial_state AS
            SELECT v.id AS video_id,v.revision AS video_revision,v.withdrawn,
              web.sha256 AS media_sha256,cur.revision AS analysis_revision,
              cur.summary AS automated,cur.evidence_hash,
              h.revision AS editorial_revision,r.public_revision,
              r.description AS human,r.media_sha256 AS bound_media_sha256,
              r.evidence_hash AS bound_evidence_hash,
              coalesce(r.public_tags,'{}'::varchar[]) AS public_tags,
              coalesce(r.description IS NOT NULL AND r.media_sha256 = web.sha256
                AND (r.evidence_hash IS NULL OR r.evidence_hash = cur.evidence_hash),false)
                AS human_active
            FROM weather.video v
            LEFT JOIN LATERAL (
              SELECT m.sha256 FROM weather.publication_asset pa
              JOIN weather.media_asset m ON m.id = pa.asset_id
              WHERE pa.publication_id = v.publication_id AND pa.role = 'web') web ON true
            LEFT JOIN LATERAL (
              SELECT ar.revision,ar.summary,ar.evidence_hash FROM weather.analysis a
              JOIN weather.analysis_revision ar ON ar.video_id = a.video_id
                AND ar.revision = a.revision AND ar.publication_id = a.publication_id
                AND ar.media_sha256 = a.media_sha256
              WHERE a.video_id = v.id AND a.media_sha256 = web.sha256) cur ON true
            LEFT JOIN weather.editorial_subject h ON h.subject_id = v.id
            LEFT JOIN weather.editorial_revision r
              ON r.subject_id = h.subject_id AND r.revision = h.revision""",
        # Public: the active human description, the editorial revision and public tags only.
        # Never favorites, private tags, notes, actors, reasons or bindings.
        """CREATE VIEW public_api.video_editorial WITH (security_barrier=true) AS
            SELECT s.video_id,coalesce(s.public_revision,0) AS revision,
              CASE WHEN s.human_active THEN s.human END AS description,
              s.human_active AS human_edited,s.public_tags AS tags
            FROM weather.video_editorial_state s
            JOIN public_api.videos v ON v.id = s.video_id AND NOT v.withdrawn""",
        "REVOKE ALL ON weather.editorial_operator,weather.editorial_subject,"
        "weather.editorial_revision,weather.editorial_audit,weather.video_editorial_state"
        " FROM PUBLIC",
        "REVOKE ALL ON public_api.video_editorial FROM PUBLIC",
        "GRANT SELECT ON public_api.video_editorial TO weather_api",
        # The AI/search projection (worker) reads the effective state, never editorial tables.
        "GRANT SELECT ON weather.video_editorial_state TO weather_worker",
        # weather_editor: editorial writes, the #41 event hooks, #25's replace/withdraw and the
        # private receipts read. No DDL, no telemetry or catalog writes.
        "GRANT USAGE ON SCHEMA weather TO weather_editor",
        "GRANT SELECT ON weather.editorial_operator TO weather_editor",
        "GRANT SELECT,INSERT ON weather.editorial_subject,weather.editorial_revision,"
        "weather.editorial_audit TO weather_editor",
        "GRANT UPDATE (revision,updated_at) ON weather.editorial_subject TO weather_editor",
        "GRANT SELECT ON weather.video_editorial_state,weather.video,weather.publication_asset,"
        "weather.analysis,weather.analysis_revision TO weather_editor",
        "GRANT SELECT,INSERT ON weather.weather_event,weather.weather_event_revision,"
        "weather.weather_event_day,weather.weather_day TO weather_editor",
        # Manual events ensure their calendar rows exist (ON CONFLICT needs SELECT).
        "GRANT SELECT,INSERT ON weather.hour_slot TO weather_editor",
        "GRANT UPDATE (revision) ON weather.weather_event TO weather_editor",
        "GRANT SELECT,INSERT ON weather.outbox,weather.job TO weather_editor",
        "GRANT SELECT,INSERT ON weather.search_document TO weather_editor",
        "GRANT UPDATE (visibility,withdrawn_at,index_status) ON weather.search_document"
        " TO weather_editor",
        "GRANT SELECT (id,received_at,inserted_at,raw_bytes,sha256,parse_state,parse_reason,"
        "parser_revision) ON weather.telemetry_receipt TO weather_editor",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
