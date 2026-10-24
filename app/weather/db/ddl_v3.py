# SPDX-License-Identifier: MIT
"""Current pointers and a commit-serialized telemetry publication boundary."""

from .schema_v3 import ALL_FAMILIES, BINDINGS, EVIDENCE_TABLES, PUBLIC_FAMILIES

# Exact v2 qualification vocabulary. Keep the existing body and OID: only the
# publication lock before its canonical-row lock is new in revision 0003.
CONFLICT_FIELDS_V2 = (
    "air_temperature",
    "battery",
    "debug",
    "distance",
    "energy",
    "firmware_revision",
    "hub_rssi",
    "illuminance",
    "lightning_strike_avg_distance",
    "lightning_strike_count",
    "local_day_rain_accumulation",
    "precip_accumulated",
    "precipitation_type",
    "radio_i2c_bus_error_count",
    "radio_network_id",
    "radio_reboot_count",
    "radio_status",
    "radio_version",
    "relative_humidity",
    "report_interval",
    "reset_flags",
    "rssi",
    "sensor_status",
    "seq",
    "solar_radiation",
    "station_pressure",
    "uptime",
    "uv",
    "voltage",
    "wind_avg",
    "wind_direction",
    "wind_gust",
    "wind_lull",
    "wind_sample_interval",
    "wind_speed",
)


def lock_statement() -> str:
    # Receipts first matches the supported Drain lock order. Migration locks all
    # predecessors before installing triggers or backfilling committed history.
    names = (*EVIDENCE_TABLES, *ALL_FAMILIES)
    return (
        "LOCK TABLE "
        + ",".join("weather." + name for name in names)
        + (" IN ACCESS EXCLUSIVE MODE")
    )


def statements() -> list[str]:
    families = ",".join("'" + name + "'" for name in PUBLIC_FAMILIES)
    all_families = ",".join("'" + name + "'" for name in ALL_FAMILIES)
    conflict_fields = ",".join("'" + name + "'" for name in CONFLICT_FIELDS_V2)
    bindings = ",".join(
        f"('{metric}','{family}','{field}','{unit}')"
        for metric, field, unit, sources in BINDINGS
        for family in sources
    )
    result = [
        "INSERT INTO weather.telemetry_publication_clock(singleton,published_through) "
        "VALUES(true,0)",
        "INSERT INTO weather.metric_binding(metric,family,field,canonical_unit) VALUES " + bindings,
        """CREATE FUNCTION weather.lock_telemetry_publication() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
          PERFORM singleton FROM weather.telemetry_publication_clock
            WHERE singleton FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'publication clock unavailable'; END IF;
          RETURN NULL;
        END; $$""",
        """CREATE FUNCTION weather.telemetry_field_effective
          (p_values jsonb,p_quality jsonb,p_field text) RETURNS jsonb
        LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
          SELECT jsonb_build_object(
            'value',public_api.telemetry_metric_values(p_values,p_quality)->p_field,
            'field_quality',public_api.telemetry_field_quality(p_quality)->p_field,
            'quality',public_api.public_metric_quality(p_quality))
        $$""",
        f"""CREATE FUNCTION weather.publish_telemetry_observation
          (p_family text,p_id uuid,p_device uuid,p_source timestamptz,p_quality jsonb) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE seq bigint; station uuid;
        BEGIN
          IF p_family NOT IN ({families}) THEN
            RAISE EXCEPTION 'unsupported public observation family';
          END IF;
          -- The BEFORE STATEMENT trigger acquired this row before receipt and
          -- canonical locks. This update is transactional, not nextval().
          IF EXISTS(SELECT 1 FROM weather.telemetry_publication
                    WHERE family=p_family AND observation_id=p_id) THEN RETURN; END IF;
          UPDATE weather.telemetry_publication_clock
            SET published_through=published_through+1 WHERE singleton
            RETURNING published_through INTO STRICT seq;
          INSERT INTO weather.telemetry_publication(publication_seq,family,observation_id)
            VALUES(seq,p_family,p_id);
          IF NOT coalesce(p_quality->'source_time_eligible'='true'::jsonb,false) THEN
            RETURN;
          END IF;
          SELECT station_id INTO STRICT station FROM weather.device WHERE id=p_device;
          INSERT INTO weather.metric_current
            (station_id,metric,family,observation_id,source_ts,revision)
          SELECT station,b.metric,p_family,p_id,p_source,1 FROM weather.metric_binding b
            WHERE b.family=p_family ORDER BY b.metric
          ON CONFLICT(station_id,metric) DO UPDATE
            SET family=EXCLUDED.family,observation_id=EXCLUDED.observation_id,
                source_ts=EXCLUDED.source_ts,revision=weather.metric_current.revision+1
            WHERE EXCLUDED.source_ts>weather.metric_current.source_ts;
        END; $$""",
        """CREATE FUNCTION weather.publish_inserted_telemetry() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
          PERFORM weather.publish_telemetry_observation(TG_TABLE_NAME,NEW.id,NEW.device_id,
            NEW.source_ts,NEW.quality);
          RETURN NULL;
        END; $$""",
        """CREATE FUNCTION weather.reject_canonical_telemetry_change() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
          IF ROW(NEW.id,NEW.device_id,NEW.receipt_id,NEW.message_type,NEW.source_ts,
                 NEW.row_idx,NEW."values") IS DISTINCT FROM
             ROW(OLD.id,OLD.device_id,OLD.receipt_id,OLD.message_type,OLD.source_ts,
                 OLD.row_idx,OLD."values") THEN
            RAISE EXCEPTION 'canonical telemetry is immutable' USING ERRCODE='55000';
          END IF;
          RETURN NEW;
        END; $$""",
        """CREATE FUNCTION weather.refresh_current_telemetry_quality() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
          UPDATE weather.metric_current c SET revision=c.revision+1
          FROM weather.metric_binding b
          WHERE c.metric=b.metric AND c.family=b.family AND c.family=TG_TABLE_NAME
            AND c.observation_id=NEW.id
            AND weather.telemetry_field_effective(OLD."values",OLD.quality,b.field)
              IS DISTINCT FROM
                weather.telemetry_field_effective(NEW."values",NEW.quality,b.field);
          RETURN NULL;
        END; $$""",
        f"""CREATE OR REPLACE FUNCTION weather.qualify_telemetry_conflict(p_incident uuid)
        RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE incident weather.observation_conflict; item text; q jsonb;
                changed boolean := false;
        BEGIN
          PERFORM singleton FROM weather.telemetry_publication_clock
            WHERE singleton FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'publication clock unavailable'; END IF;
          SELECT * INTO STRICT incident FROM weather.observation_conflict WHERE id=p_incident;
          IF incident.family NOT IN ({all_families}) OR cardinality(incident.fields)>64 THEN
            RAISE EXCEPTION 'invalid conflict qualification';
          END IF;
          EXECUTE format('SELECT quality FROM weather.%I '
                         'WHERE id=$1 AND receipt_id=$2 AND source_ts=$3 FOR UPDATE',
                         incident.family)
            INTO STRICT q USING incident.observation_id,incident.first_receipt_id,
                                incident.source_ts;
          FOREACH item IN ARRAY incident.fields LOOP
            IF item IS NULL OR item NOT IN ({conflict_fields})
               OR NOT (coalesce(q->'fields','{{}}'::jsonb) ? item) THEN
              RAISE EXCEPTION 'invalid conflicting field';
            END IF;
            q := jsonb_set(q,ARRAY['fields',item],(q->'fields'->item) ||
                 jsonb_build_object('eligible',false,'valid',false,'reason','payload_conflict'));
            changed := true;
          END LOOP;
          IF changed THEN
            EXECUTE format('UPDATE weather.%I SET quality=$1 WHERE id=$2',incident.family)
              USING q,incident.observation_id;
            INSERT INTO weather.outbox(topic,idempotency_key,payload,not_before,deadline)
              VALUES('telemetry.conflict','telemetry-conflict:'||incident.id,
                jsonb_build_object('incident_id',incident.id,'family',incident.family,
                  'observation_id',incident.observation_id,'source_ts',incident.source_ts,
                  'fields',to_jsonb(incident.fields)),clock_timestamp(),
                  clock_timestamp()+interval '1 day') ON CONFLICT(idempotency_key) DO NOTHING;
          END IF;
        END; $$""",
        """CREATE VIEW public_api.metric_current WITH(security_barrier=true) AS
        SELECT s.alias AS station_alias,c.metric,b.canonical_unit AS unit,
          o.id AS observation_id,o.device_alias,o.message_type,
          o.observed_at,o.received_at,o.row_idx,
          o."values"->b.field AS value,o.field_quality->b.field AS field_quality,
          o.quality,c.revision
        FROM weather.metric_current c
        JOIN weather.station s ON s.id=c.station_id
        JOIN weather.metric_binding b ON b.metric=c.metric AND b.family=c.family
        JOIN public_api.telemetry_observations o
          ON o.id=c.observation_id AND o.message_type=c.family""",
        """CREATE VIEW public_api.telemetry_publications WITH(security_barrier=true) AS
        SELECT p.publication_seq,o.id,o.station_alias,o.device_alias,o.message_type,
          o.observed_at,o.received_at,o.row_idx,o."values",o.field_quality,o.quality
        FROM weather.telemetry_publication p JOIN public_api.telemetry_observations o
          ON o.id=p.observation_id AND o.message_type=p.family""",
        """CREATE FUNCTION public_api.telemetry_snapshot_watermark() RETURNS bigint
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE watermark bigint;
        BEGIN
          SELECT published_through INTO STRICT watermark
            FROM weather.telemetry_publication_clock WHERE singleton;
          RETURN watermark;
        END;
        $$""",
    ]
    for name in (*EVIDENCE_TABLES, *ALL_FAMILIES):
        result.append(
            f"CREATE TRIGGER telemetry_publication_lock BEFORE INSERT OR UPDATE ON weather.{name} "
            "FOR EACH STATEMENT EXECUTE FUNCTION weather.lock_telemetry_publication()"
        )
    for family in ALL_FAMILIES:
        result.append(
            f"CREATE TRIGGER canonical_telemetry_immutable BEFORE UPDATE ON weather.{family} "
            "FOR EACH ROW EXECUTE FUNCTION weather.reject_canonical_telemetry_change()"
        )
    for family in PUBLIC_FAMILIES:
        result.extend(
            [
                f"CREATE TRIGGER telemetry_published AFTER INSERT ON weather.{family} "
                "FOR EACH ROW EXECUTE FUNCTION weather.publish_inserted_telemetry()",
                f"CREATE TRIGGER current_telemetry_quality AFTER UPDATE OF quality "
                f"ON weather.{family} FOR EACH ROW WHEN (OLD.quality IS DISTINCT FROM NEW.quality) "
                "EXECUTE FUNCTION weather.refresh_current_telemetry_quality()",
            ]
        )
    union = " UNION ALL ".join(
        f"SELECT '{family}'::text AS family,id,device_id,source_ts,quality FROM weather.{family}"
        for family in PUBLIC_FAMILIES
    )
    result.extend(
        [
            f"""DO $$ DECLARE o record; BEGIN
            FOR o IN SELECT * FROM ({union}) AS history ORDER BY source_ts,family,id LOOP
              PERFORM weather.publish_telemetry_observation(o.family,o.id,o.device_id,
                o.source_ts,o.quality);
            END LOOP;
            END; $$""",
            "REVOKE ALL ON ALL TABLES IN SCHEMA weather FROM PUBLIC",
            "REVOKE ALL ON FUNCTION weather.lock_telemetry_publication(),"
            "weather.telemetry_field_effective(jsonb,jsonb,text),"
            "weather.publish_telemetry_observation(text,uuid,uuid,timestamptz,jsonb),"
            "weather.publish_inserted_telemetry(),weather.reject_canonical_telemetry_change(),"
            "weather.refresh_current_telemetry_quality(),weather.qualify_telemetry_conflict(uuid) "
            "FROM PUBLIC",
            "GRANT EXECUTE ON FUNCTION weather.qualify_telemetry_conflict(uuid) TO weather_ingest",
            "REVOKE ALL ON FUNCTION public_api.telemetry_snapshot_watermark() FROM PUBLIC",
            "GRANT EXECUTE ON FUNCTION public_api.telemetry_snapshot_watermark() "
            "TO weather_api,weather_worker",
            "GRANT SELECT ON public_api.metric_current,public_api.telemetry_publications "
            "TO weather_api,weather_worker",
        ]
    )
    return result
