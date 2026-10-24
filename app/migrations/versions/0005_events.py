# SPDX-License-Identifier: MIT
"""Weather events (#41): logical events, immutable revisions, day links and public views.

Expand-only on top of 0004_search. Inline DDL; never import application modules here.

A logical event is only an identity (opaque public id, private logical key, rule family) and a
pointer to its current revision. Every content change, including withdrawal, is a new immutable
revision. A trigger stamps each revision with its writing transaction id, so the public feed can
page over an exact MVCC snapshot (pg_visible_in_snapshot) without repeating or dropping events
while revisions land. Day links belong to one revision and are immutable too.

The carried slice-3 CHECKs: origin/basis provenance and a non-empty half-open source interval.
"""

from alembic import op

revision = "0005_events"
down_revision = "0004_search"
branch_labels = None
depends_on = None

RULE_FAMILIES = (
    "precipitation_onset",
    "daily_gust_max",
    "lightning_strike",
    "lightning_interval",
    "visual",
    "synthesis",
    "manual",
)
CAMERAS = ("skycam_east", "skycam_west", "frontyard_1")
# Telemetry columns the worker's event rules read; never receipts, serials or configuration.
EVIDENCE_COLUMNS = 'id,device_id,source_ts,"values",quality'
PUBLIC_COLUMNS = """r.event_id AS id,r.revision,r.recorded_xid,e.rule_family AS kind,r.origin,
  r.basis,r.camera_id AS camera,r.starts_at,r.ends_at,r.time_precision,r.rule_revision,
  r.author_kind='operator' AS human_edited,r.label,r.measurement,r.evidence_ids,
  r.recorded_at AS updated_at"""


def _in(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def statements() -> list[str]:
    return [
        f"""CREATE TABLE weather.weather_event (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            logical_key varchar(256) NOT NULL,
            rule_family varchar(32) NOT NULL,
            revision integer DEFAULT 0 NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_weather_event PRIMARY KEY (id),
            CONSTRAINT uq_weather_event_logical_key UNIQUE (logical_key),
            CONSTRAINT ck_weather_event_rule_family CHECK (rule_family IN ({_in(RULE_FAMILIES)})),
            CONSTRAINT ck_weather_event_revision CHECK (revision >= 0)
        )""",
        f"""CREATE TABLE weather.weather_event_revision (
            event_id uuid NOT NULL,
            revision integer NOT NULL,
            recorded_xid bigint NOT NULL,
            recorded_at timestamptz NOT NULL,
            visibility varchar(16) NOT NULL,
            withdrawal_reason varchar(32),
            origin varchar(16) NOT NULL,
            basis varchar(16) NOT NULL,
            camera_id varchar(32),
            starts_at timestamptz NOT NULL,
            ends_at timestamptz NOT NULL,
            time_precision varchar(16) NOT NULL,
            rule_revision varchar(64) NOT NULL,
            author_kind varchar(16) NOT NULL,
            author varchar(256),
            reason text,
            label varchar(240),
            measurement jsonb DEFAULT '{{}}'::jsonb NOT NULL,
            evidence_ids uuid[] DEFAULT '{{}}'::uuid[] NOT NULL,
            evidence_hash varchar(64) NOT NULL,
            CONSTRAINT pk_weather_event_revision PRIMARY KEY (event_id, revision),
            CONSTRAINT fk_weather_event_revision_event_id_weather_event FOREIGN KEY (event_id)
              REFERENCES weather.weather_event (id) ON DELETE RESTRICT,
            CONSTRAINT ck_weather_event_revision_number CHECK (revision > 0),
            CONSTRAINT ck_weather_event_revision_interval CHECK (starts_at < ends_at),
            CONSTRAINT ck_weather_event_revision_visibility CHECK
              (visibility IN ('public','private','withdrawn')
               AND (visibility='withdrawn') = (withdrawal_reason IS NOT NULL)),
            CONSTRAINT ck_weather_event_revision_provenance CHECK (
              (origin='measured' AND basis='instrument' AND author_kind IN ('rule','operator'))
              OR (origin='visual' AND basis='visual' AND author_kind IN ('model','operator'))
              OR (origin='synthesis' AND basis='inference' AND author_kind IN ('model','operator'))
              OR (origin='manual' AND basis IN ('instrument','visual','inference')
                  AND author_kind='operator')),
            CONSTRAINT ck_weather_event_revision_author CHECK ((author_kind='operator') =
              (author IS NOT NULL AND reason IS NOT NULL)
              AND (author IS NULL OR length(author) BETWEEN 1 AND 256)
              AND (reason IS NULL OR length(reason) BETWEEN 1 AND 2000)),
            CONSTRAINT ck_weather_event_revision_precision CHECK
              (time_precision IN ('second','interval','clip')
               AND (origin NOT IN ('visual','synthesis') OR time_precision='clip')),
            CONSTRAINT ck_weather_event_revision_camera CHECK
              (camera_id IS NULL OR camera_id IN ({_in(CAMERAS)})),
            CONSTRAINT ck_weather_event_revision_label CHECK
              ((origin='measured' OR label IS NOT NULL)
               AND (label IS NULL OR length(label) BETWEEN 1 AND 240)),
            CONSTRAINT ck_weather_event_revision_content CHECK
              (jsonb_typeof(measurement)='object' AND octet_length(measurement::text) <= 4096
               AND cardinality(evidence_ids) <= 256 AND evidence_hash ~ '^[0-9a-f]{{64}}$')
        )""",
        """CREATE INDEX ix_weather_event_revision_starts_at
           ON weather.weather_event_revision (starts_at, event_id)""",
        """CREATE TABLE weather.weather_event_day (
            event_id uuid NOT NULL,
            revision integer NOT NULL,
            local_date date NOT NULL,
            CONSTRAINT pk_weather_event_day PRIMARY KEY (event_id, revision, local_date),
            CONSTRAINT fk_weather_event_day_event_id_weather_event_revision
              FOREIGN KEY (event_id, revision)
              REFERENCES weather.weather_event_revision (event_id, revision) ON DELETE RESTRICT,
            CONSTRAINT fk_weather_event_day_local_date_weather_day FOREIGN KEY (local_date)
              REFERENCES weather.weather_day (local_date) ON DELETE RESTRICT
        )""",
        """CREATE INDEX ix_weather_event_day_local_date
           ON weather.weather_event_day (local_date)""",
        # Stamps the writing transaction (top-level xid) and time; revisions are immutable.
        # A public automatic revision needs nonempty unique evidence. Trigger functions run
        # without an EXECUTE grant, and default privileges keep this one private.
        """CREATE FUNCTION weather.weather_event_revision_guard() RETURNS trigger
           LANGUAGE plpgsql SET search_path=pg_catalog AS $$
           BEGIN
             IF TG_OP <> 'INSERT' THEN
               RAISE EXCEPTION 'event revisions are immutable' USING ERRCODE='55000';
             END IF;
             IF NEW.visibility='public' AND NEW.origin <> 'manual' AND (
                 cardinality(NEW.evidence_ids) = 0 OR (SELECT count(DISTINCT e) FROM
                   unnest(NEW.evidence_ids) AS e) <> cardinality(NEW.evidence_ids)) THEN
               RAISE EXCEPTION 'public automatic events need unique evidence'
                 USING ERRCODE='23514';
             END IF;
             NEW.recorded_xid := pg_current_xact_id()::text::bigint;
             NEW.recorded_at := clock_timestamp();
             RETURN NEW;
           END $$""",
        """CREATE TRIGGER weather_event_revision_guard
           BEFORE INSERT OR UPDATE OR DELETE ON weather.weather_event_revision
           FOR EACH ROW EXECUTE FUNCTION weather.weather_event_revision_guard()""",
        # Every revision's position, so a page can tell which revision a snapshot saw last.
        """CREATE VIEW public_api.event_revision_markers WITH (security_barrier=true) AS
           SELECT event_id AS id,revision,recorded_xid FROM weather.weather_event_revision""",
        # Content of public revisions only: no logical key, author, reason or private state.
        f"""CREATE VIEW public_api.event_revisions WITH (security_barrier=true) AS
           SELECT {PUBLIC_COLUMNS}
           FROM weather.weather_event_revision r JOIN weather.weather_event e ON e.id=r.event_id
           WHERE r.visibility='public' AND EXISTS (SELECT 1 FROM weather.weather_event_day d
             WHERE d.event_id=r.event_id AND d.revision=r.revision)""",
        """CREATE VIEW public_api.event_days WITH (security_barrier=true) AS
           SELECT d.event_id AS id,d.revision,d.local_date FROM weather.weather_event_day d
           JOIN weather.weather_event_revision r
             ON r.event_id=d.event_id AND r.revision=d.revision
           WHERE r.visibility='public'""",
        "REVOKE ALL ON weather.weather_event,weather.weather_event_revision,"
        "weather.weather_event_day FROM PUBLIC",
        "REVOKE ALL ON public_api.event_revision_markers,public_api.event_revisions,"
        "public_api.event_days FROM PUBLIC",
        # The scheduler (worker) derives measured events: it reads only the evidence columns
        # of the three source families and a device's station, and appends revisions.
        f"GRANT SELECT ({EVIDENCE_COLUMNS}) ON weather.evt_precip,weather.evt_strike,"
        "weather.obs_st TO weather_worker",
        "GRANT SELECT (id,station_id) ON weather.device TO weather_worker",
        "GRANT SELECT,INSERT ON weather.weather_event,weather.weather_event_revision,"
        "weather.weather_event_day TO weather_worker",
        "GRANT UPDATE (revision) ON weather.weather_event TO weather_worker",
        "GRANT SELECT ON public_api.event_revision_markers,public_api.event_revisions,"
        "public_api.event_days TO weather_api",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
