# SPDX-License-Identifier: MIT
"""Immutable public evidence snapshots sharing v3's transactional publication clock."""

from .ddl_v3 import lock_statement as lock_predecessors
from .schema_v3 import PUBLIC_FAMILIES

PROJECTION = (
    "publication_seq,id,station_alias,device_alias,message_type,observed_at,"
    'received_at,row_idx,"values",field_quality,quality'
)


def lock_statements() -> tuple[str, ...]:
    # Keep v3's receipt-before-canonical relation order. No ingest statement can
    # cross the migration while triggers/backfill are installed. Lock the same
    # clock row before assigning any retained predecessor publication positions.
    return (
        lock_predecessors(),
        "LOCK TABLE weather.telemetry_publication_clock,weather.telemetry_publication "
        "IN ACCESS EXCLUSIVE MODE",
        "SELECT singleton FROM weather.telemetry_publication_clock WHERE singleton FOR UPDATE",
    )


def backfill_statement() -> str:
    # Deliberately no clock increment or append helper: predecessors retain their
    # original boundary and migration does not enqueue a historical outbox flood.
    return (
        "INSERT INTO weather.telemetry_evidence_revision(revision_seq," + PROJECTION + ") "
        "SELECT publication_seq," + PROJECTION + " FROM public_api.telemetry_publications "
        "ORDER BY publication_seq"
    )


def statements() -> list[str]:
    result = [
        """CREATE FUNCTION weather.append_telemetry_evidence
        (p_publication_seq bigint,p_revision_seq bigint) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE projection record; instant timestamptz;
        BEGIN
          SELECT * INTO STRICT projection FROM public_api.telemetry_publications
            WHERE publication_seq=p_publication_seq;
          INSERT INTO weather.telemetry_evidence_revision
            (revision_seq,publication_seq,id,station_alias,device_alias,message_type,
             observed_at,received_at,row_idx,"values",field_quality,quality)
          VALUES(p_revision_seq,projection.publication_seq,projection.id,
             projection.station_alias,projection.device_alias,projection.message_type,
             projection.observed_at,projection.received_at,projection.row_idx,
             projection."values",projection.field_quality,projection.quality);
          instant := clock_timestamp();
          INSERT INTO weather.outbox(topic,idempotency_key,payload,not_before,deadline)
          VALUES('telemetry.changed','telemetry-evidence:'||p_revision_seq,
            jsonb_build_object('id',projection.id,'family',projection.message_type,
              'station',projection.station_alias,'sourceTime',projection.observed_at,
              'watermark',p_revision_seq),instant,instant+interval '1 day');
        END; $$""",
        """CREATE FUNCTION weather.record_inserted_telemetry_evidence() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE seq bigint;
        BEGIN
          -- telemetry_revision_published sorts after v3 telemetry_published, so
          -- the initial publication position already exists and is reused.
          SELECT publication_seq INTO STRICT seq FROM weather.telemetry_publication
            WHERE family=TG_TABLE_NAME AND observation_id=NEW.id;
          PERFORM weather.append_telemetry_evidence(seq,seq);
          RETURN NULL;
        END; $$""",
        """CREATE FUNCTION weather.record_telemetry_evidence_quality() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE publication bigint; revision bigint;
        BEGIN
          IF ROW(public_api.telemetry_metric_values(OLD."values",OLD.quality),
                 public_api.telemetry_field_quality(OLD.quality),
                 public_api.public_metric_quality(OLD.quality)) IS NOT DISTINCT FROM
             ROW(public_api.telemetry_metric_values(NEW."values",NEW.quality),
                 public_api.telemetry_field_quality(NEW.quality),
                 public_api.public_metric_quality(NEW.quality)) THEN
            RETURN NULL;
          END IF;
          -- v3's BEFORE STATEMENT trigger took this row before canonical locks.
          -- Reuse that transactional serialization; never use a SQL sequence.
          SELECT publication_seq INTO STRICT publication FROM weather.telemetry_publication
            WHERE family=TG_TABLE_NAME AND observation_id=NEW.id;
          UPDATE weather.telemetry_publication_clock
            SET published_through=published_through+1 WHERE singleton
            RETURNING published_through INTO STRICT revision;
          PERFORM weather.append_telemetry_evidence(publication,revision);
          RETURN NULL;
        END; $$""",
        """CREATE FUNCTION weather.reject_telemetry_evidence_change() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
          RAISE EXCEPTION 'telemetry evidence revisions are immutable' USING ERRCODE='55000';
        END; $$""",
        "CREATE TRIGGER telemetry_evidence_immutable BEFORE UPDATE OR DELETE "
        "ON weather.telemetry_evidence_revision FOR EACH ROW "
        "EXECUTE FUNCTION weather.reject_telemetry_evidence_change()",
    ]
    for family in PUBLIC_FAMILIES:
        result.extend(
            [
                f"CREATE TRIGGER telemetry_revision_published AFTER INSERT ON weather.{family} "
                "FOR EACH ROW EXECUTE FUNCTION weather.record_inserted_telemetry_evidence()",
                f"CREATE TRIGGER telemetry_revision_quality AFTER UPDATE OF quality "
                f"ON weather.{family} FOR EACH ROW WHEN (OLD.quality IS DISTINCT FROM NEW.quality) "
                "EXECUTE FUNCTION weather.record_telemetry_evidence_quality()",
            ]
        )
    result.extend(
        [
            backfill_statement(),
            "CREATE VIEW public_api.telemetry_evidence_revisions WITH(security_barrier=true) AS "
            "SELECT revision_seq," + PROJECTION + " FROM weather.telemetry_evidence_revision",
            "REVOKE ALL ON weather.telemetry_evidence_revision "
            "FROM PUBLIC,weather_api,weather_worker,weather_ingest,weather_editor",
            "REVOKE ALL ON public_api.telemetry_evidence_revisions "
            "FROM PUBLIC,weather_ingest,weather_editor",
            "GRANT SELECT ON public_api.telemetry_evidence_revisions TO weather_api,weather_worker",
            "REVOKE ALL ON FUNCTION weather.append_telemetry_evidence(bigint,bigint),"
            "weather.record_inserted_telemetry_evidence(),weather.record_telemetry_evidence_quality(),"
            "weather.reject_telemetry_evidence_change() "
            "FROM PUBLIC,weather_api,weather_worker,weather_ingest,weather_editor",
        ]
    )
    return result
