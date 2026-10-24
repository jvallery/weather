# SPDX-License-Identifier: MIT
"""Slice-1 baseline: schemas, telemetry, weather_day, jobs/outbox, one public view, grants.

Inline DDL only; never import application modules here. Once applied to production this
revision is frozen and later changes are expand/contract revisions. The removed slice-2/3
DDL is recorded in docs/operations/database.md.
"""

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

FAMILIES = (
    "obs_st",
    "rapid_wind",
    "evt_precip",
    "evt_strike",
    "device_status",
    "hub_status",
    "obs_air",
    "obs_sky",
)
# Status families stay private; the public view unions only these.
PUBLIC_FAMILIES = ("obs_st", "rapid_wind", "evt_precip", "evt_strike", "obs_air", "obs_sky")
# Current conditions read the newest row, so these get an ordered btree instead of BRIN.
CURRENT_FAMILIES = ("obs_st", "rapid_wind")
PUBLIC_METRIC_KEYS = (
    "air_temperature",
    "relative_humidity",
    "wind_lull",
    "wind_avg",
    "wind_gust",
    "wind_direction",
    "wind_sample_interval",
    "station_pressure",
    "illuminance",
    "uv",
    "solar_radiation",
    "precip_accumulated",
    "precipitation_type",
    "lightning_strike_avg_distance",
    "lightning_strike_count",
    "report_interval",
    "temperature",
    "humidity",
    "wind_speed",
    "pressure",
    "precipitation",
    "distance",
    "energy",
    "wind_direction_degrees",
    "rain_accumulated",
    "timestamp_uncertainty_seconds",
)
PAYLOAD_CHECK = "jsonb_typeof(payload)='object' AND octet_length(payload::text) <= 1048576"


def family_table(family: str) -> list[str]:
    index = (
        f"CREATE INDEX ix_{family}_source_ts ON weather.{family} (source_ts)"
        if family in CURRENT_FAMILIES
        else f"CREATE INDEX ix_{family}_source_brin ON weather.{family} USING brin (source_ts)"
    )
    return [
        f"""CREATE TABLE weather.{family} (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            device_id uuid NOT NULL,
            receipt_id uuid NOT NULL,
            message_type varchar(32) DEFAULT '{family}' NOT NULL,
            source_ts timestamptz NOT NULL,
            row_idx integer NOT NULL,
            "values" jsonb DEFAULT '{{}}'::jsonb NOT NULL,
            quality jsonb DEFAULT '{{}}'::jsonb NOT NULL,
            CONSTRAINT pk_{family} PRIMARY KEY (id),
            CONSTRAINT uq_{family}_device_id_message_type_source_ts_row_idx
              UNIQUE (device_id, message_type, source_ts, row_idx),
            CONSTRAINT ck_{family}_family CHECK (message_type = '{family}'),
            CONSTRAINT ck_{family}_row_index CHECK (row_idx >= 0),
            CONSTRAINT ck_{family}_values_object CHECK (jsonb_typeof("values") = 'object'),
            CONSTRAINT fk_{family}_device_id_device FOREIGN KEY (device_id)
              REFERENCES weather.device (id) ON DELETE RESTRICT,
            CONSTRAINT fk_{family}_receipt_id_telemetry_receipt FOREIGN KEY (receipt_id)
              REFERENCES weather.telemetry_receipt (id) ON DELETE RESTRICT
        )""",
        index,
    ]


def statements() -> list[str]:
    """One PostgreSQL statement per item; function bodies may contain semicolons."""
    metrics = ",".join(f"'{key}'" for key in PUBLIC_METRIC_KEYS)
    observations = " UNION ALL ".join(
        f"""SELECT o.id,s.alias AS station_alias,d.alias AS device_alias,o.message_type,
            o.source_ts AS observed_at,r.received_at,o.row_idx,
            public_api.public_metric_values(o."values",o.quality) AS "values",
            public_api.public_metric_quality(o.quality) AS quality
            FROM weather.{family} o JOIN weather.device d ON d.id=o.device_id
            JOIN weather.station s ON s.id=d.station_id
            JOIN weather.telemetry_receipt r ON r.id=o.receipt_id"""
        for family in PUBLIC_FAMILIES
    )
    telemetry = ", ".join(
        f"weather.{name}" for name in ("station", "device", "telemetry_receipt", *FAMILIES)
    )
    return [
        "CREATE SCHEMA weather",
        "CREATE SCHEMA public_api",
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
        """CREATE TABLE weather.station (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            alias varchar(64) NOT NULL,
            site varchar(64) NOT NULL,
            private_configuration jsonb DEFAULT '{}'::jsonb NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_station PRIMARY KEY (id),
            CONSTRAINT uq_station_alias UNIQUE (alias),
            CONSTRAINT uq_station_site UNIQUE (site)
        )""",
        """CREATE TABLE weather.device (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            station_id uuid NOT NULL,
            alias varchar(64) NOT NULL,
            private_id text NOT NULL,
            kind varchar(32) NOT NULL,
            valid_from timestamptz NOT NULL,
            valid_until timestamptz,
            private_configuration jsonb DEFAULT '{}'::jsonb NOT NULL,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_device PRIMARY KEY (id),
            CONSTRAINT ck_device_validity CHECK (valid_until IS NULL OR valid_until > valid_from),
            CONSTRAINT fk_device_station_id_station FOREIGN KEY (station_id)
              REFERENCES weather.station (id) ON DELETE RESTRICT,
            CONSTRAINT uq_device_alias UNIQUE (alias),
            CONSTRAINT uq_device_private_id UNIQUE (private_id)
        )""",
        """CREATE TABLE weather.telemetry_receipt (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            received_at timestamptz NOT NULL,
            raw_bytes bytea NOT NULL,
            sha256 varchar(64) NOT NULL,
            parse_state varchar(32) NOT NULL,
            parse_reason varchar(64),
            parser_revision varchar(64) NOT NULL,
            receiver_incarnation varchar(128) NOT NULL,
            CONSTRAINT pk_telemetry_receipt PRIMARY KEY (id),
            CONSTRAINT ck_telemetry_receipt_digest CHECK (sha256 ~ '^[0-9a-f]{64}$'
              AND sha256=encode(pg_catalog.sha256(raw_bytes),'hex')),
            CONSTRAINT ck_telemetry_receipt_parse_state
              CHECK (parse_state IN ('parsed','unsupported','quarantined'))
        )""",
        # Btree, not BRIN: reproject pages by the (received_at, id) keyset.
        """CREATE INDEX ix_telemetry_receipt_received_id
           ON weather.telemetry_receipt (received_at, id)""",
        *(statement for family in FAMILIES for statement in family_table(family)),
        # One row per America/Denver calendar day (23, 24 or 25 hours).
        """CREATE TABLE weather.weather_day (
            local_date date NOT NULL,
            starts_at timestamptz NOT NULL,
            ends_at timestamptz NOT NULL,
            CONSTRAINT pk_weather_day PRIMARY KEY (local_date),
            CONSTRAINT ck_weather_day_interval CHECK (ends_at - starts_at IN
              (interval '23 hours',interval '24 hours',interval '25 hours'))
        )""",
        f"""CREATE TABLE weather.job (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            kind varchar(128) NOT NULL,
            idempotency_key varchar(256) NOT NULL,
            payload jsonb DEFAULT '{{}}'::jsonb NOT NULL,
            status varchar(16) DEFAULT 'queued' NOT NULL,
            attempt integer DEFAULT 0 NOT NULL,
            not_before timestamptz NOT NULL,
            deadline timestamptz NOT NULL,
            lease_until timestamptz,
            last_error varchar(128),
            result jsonb,
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_job PRIMARY KEY (id),
            CONSTRAINT ck_job_attempt CHECK (attempt >= 0),
            CONSTRAINT ck_job_status CHECK
              (status IN ('queued','running','succeeded','failed','cancelled','expired')),
            CONSTRAINT ck_job_lease CHECK ((status='running') = (lease_until IS NOT NULL)
              AND (lease_until IS NULL OR lease_until <= deadline)),
            CONSTRAINT ck_job_payload CHECK ({PAYLOAD_CHECK}),
            CONSTRAINT uq_job_idempotency_key UNIQUE (idempotency_key)
        )""",
        """CREATE INDEX ix_job_claim ON weather.job (not_before, created_at)
           WHERE status IN ('queued','running')""",
        f"""CREATE TABLE weather.outbox (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            topic varchar(128) NOT NULL,
            idempotency_key varchar(256) NOT NULL,
            payload jsonb DEFAULT '{{}}'::jsonb NOT NULL,
            not_before timestamptz NOT NULL,
            deadline timestamptz NOT NULL,
            dispatched_at timestamptz,
            job_id uuid,
            error varchar(64),
            created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT pk_outbox PRIMARY KEY (id),
            CONSTRAINT ck_outbox_quarantine
              CHECK (error IS NULL OR (dispatched_at IS NOT NULL AND job_id IS NULL)),
            CONSTRAINT ck_outbox_payload CHECK ({PAYLOAD_CHECK}),
            CONSTRAINT uq_outbox_idempotency_key UNIQUE (idempotency_key),
            CONSTRAINT fk_outbox_job_id_job FOREIGN KEY (job_id)
              REFERENCES weather.job (id) ON DELETE RESTRICT
        )""",
        """CREATE INDEX ix_outbox_pending ON weather.outbox (created_at, id)
           WHERE dispatched_at IS NULL""",
        """CREATE FUNCTION weather.reject_deadline_change() RETURNS trigger
           LANGUAGE plpgsql SET search_path=pg_catalog AS $$
           BEGIN
             IF NEW.deadline IS DISTINCT FROM OLD.deadline THEN
               RAISE EXCEPTION 'original job deadline is immutable' USING ERRCODE='55000';
             END IF;
             RETURN NEW;
           END $$""",
        """CREATE TRIGGER job_deadline_immutable BEFORE UPDATE OF deadline ON weather.job
           FOR EACH ROW EXECUTE FUNCTION weather.reject_deadline_change()""",
        """CREATE TRIGGER outbox_deadline_immutable BEFORE UPDATE OF deadline ON weather.outbox
           FOR EACH ROW EXECUTE FUNCTION weather.reject_deadline_change()""",
        # A value is public only when its row is valid and the field is not listed invalid;
        # malformed quality fails closed to null.
        f"""CREATE FUNCTION public_api.public_metric_values(p_values jsonb,p_quality jsonb)
            RETURNS jsonb LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
            SELECT coalesce(jsonb_object_agg(key,CASE
              WHEN coalesce(p_quality->'valid'='true'::jsonb,false)
               AND jsonb_typeof(p_quality->'invalid')='array'
               AND NOT p_quality->'invalid' ? key
               AND jsonb_typeof(value)='number' THEN value ELSE 'null'::jsonb END),'{{}}'::jsonb)
            FROM jsonb_each(p_values) WHERE key IN ({metrics}) $$""",
        f"""CREATE FUNCTION public_api.public_metric_quality(p_quality jsonb) RETURNS jsonb
            LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
            SELECT jsonb_build_object('valid',coalesce(p_quality->'valid'='true'::jsonb,false),
              'reason',CASE WHEN p_quality->>'reason' IN ('valid','future_timestamp')
                THEN p_quality->>'reason' ELSE 'unknown' END,
              'invalid',coalesce((SELECT jsonb_agg(name ORDER BY name)
                FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(p_quality->'invalid')='array'
                  THEN p_quality->'invalid' ELSE '[]'::jsonb END) AS fields(name)
                WHERE name IN ({metrics})),'[]'::jsonb)) $$""",
        # Public aliases and gated values only: no receipts, serials or status families.
        "CREATE VIEW public_api.observations WITH (security_barrier=true) AS " + observations,
        # Least privilege: ingest writes telemetry, worker writes days and jobs, api reads the
        # public view, weather_editor has nothing until #44, and the migrator owns everything.
        "REVOKE ALL ON SCHEMA weather,public_api FROM PUBLIC",
        "REVOKE ALL ON ALL TABLES IN SCHEMA weather,public_api FROM PUBLIC",
        "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA weather,public_api FROM PUBLIC",
        "ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC",
        "GRANT USAGE ON SCHEMA weather TO weather_ingest,weather_worker",
        "GRANT USAGE ON SCHEMA public_api TO weather_api",
        f"GRANT SELECT,INSERT ON {telemetry} TO weather_ingest",
        "GRANT SELECT,INSERT ON weather.weather_day TO weather_worker",
        "GRANT SELECT,INSERT,UPDATE,DELETE ON weather.job,weather.outbox TO weather_worker",
        "GRANT SELECT ON public_api.observations TO weather_api",
        "GRANT EXECUTE ON FUNCTION public_api.public_metric_values(jsonb,jsonb),"
        "public_api.public_metric_quality(jsonb) TO weather_api",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
