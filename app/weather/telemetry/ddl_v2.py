# SPDX-License-Identifier: MIT
"""Narrow qualification writes and opaque conflict outbox; no canonical values updates."""

from .schema_v2 import TABLE_NAMES

# Immutable allowlists for revision 0002; later parser revisions do not change migration DDL.
FAMILIES = (
    "device_status",
    "evt_precip",
    "evt_strike",
    "hub_status",
    "obs_air",
    "obs_sky",
    "obs_st",
    "rapid_wind",
)
FIELDS = (
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


def statements() -> list[str]:
    families = ",".join("'" + name + "'" for name in FAMILIES)
    fields = ",".join("'" + name + "'" for name in FIELDS)
    result = [
        f"""CREATE FUNCTION weather.qualify_telemetry_conflict(p_incident uuid)
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,weather AS $$
        DECLARE incident weather.observation_conflict; item text; q jsonb;
                changed boolean := false;
        BEGIN
          SELECT * INTO STRICT incident FROM weather.observation_conflict WHERE id=p_incident;
          IF incident.family NOT IN ({families}) OR cardinality(incident.fields)>64 THEN
            RAISE EXCEPTION 'invalid conflict qualification';
          END IF;
          EXECUTE format('SELECT quality FROM weather.%I WHERE id=$1 AND receipt_id=$2 AND source_ts=$3 FOR UPDATE',
                         incident.family)
            INTO STRICT q USING incident.observation_id,incident.first_receipt_id,incident.source_ts;
          FOREACH item IN ARRAY incident.fields LOOP
            IF item IS NULL OR item NOT IN ({fields}) OR NOT (coalesce(q->'fields','{{}}'::jsonb) ? item) THEN
              RAISE EXCEPTION 'invalid conflicting field';
            END IF;
            q := jsonb_set(q,ARRAY['fields',item],(q->'fields'->item) ||
                 '{{"eligible":false,"valid":false,"reason":"payload_conflict"}}'::jsonb);
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
        END $$""",
        """CREATE FUNCTION weather.mark_strike_multiplicity(p_observation uuid)
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,weather AS $$
        BEGIN
          IF (SELECT count(*) FROM weather.receipt_row_provenance
              WHERE family='evt_strike' AND observation_id=p_observation)>1 THEN
            UPDATE weather.evt_strike SET quality=jsonb_set(quality,
              '{event_multiplicity}','"ambiguous"'::jsonb) WHERE id=p_observation;
          END IF;
        END $$""",
        """CREATE VIEW weather.telemetry_coverage_minute WITH(security_barrier=true) AS
        SELECT t.device_id,t.family,date_trunc('minute',r.received_at) AS minute,
          count(*) AS received_datagrams,
          min((t.cadence->>'seconds')::numeric) AS reported_min_cadence_seconds,
          max((t.cadence->>'seconds')::numeric) AS reported_max_cadence_seconds,
          count(*) FILTER(WHERE r.parse_state='quarantined') AS quarantined_datagrams,
          count(*) FILTER(WHERE r.parse_state='unsupported') AS unsupported_datagrams
        FROM weather.receipt_transport t JOIN weather.telemetry_receipt r ON r.id=t.receipt_id
        GROUP BY t.device_id,t.family,date_trunc('minute',r.received_at)""",
    ]
    metric_keys = tuple(
        name
        for name in FIELDS
        if name
        not in {
            "battery",
            "uptime",
            "voltage",
            "firmware_revision",
            "rssi",
            "hub_rssi",
            "sensor_status",
            "debug",
            "reset_flags",
            "seq",
            "radio_version",
            "radio_reboot_count",
            "radio_i2c_bus_error_count",
            "radio_status",
            "radio_network_id",
        }
    )
    metrics = ",".join("'" + name + "'" for name in metric_keys)
    result.extend(
        [
            f"""CREATE FUNCTION public_api.telemetry_metric_values(p_values jsonb) RETURNS jsonb
        LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
        SELECT coalesce(jsonb_object_agg(key,value),'{{}}'::jsonb)
        FROM jsonb_each(CASE WHEN jsonb_typeof(p_values)='object' THEN p_values
                            ELSE '{{}}'::jsonb END)
        WHERE key IN ({metrics}) AND jsonb_typeof(value) IN ('number','null') $$""",
            f"""CREATE FUNCTION public_api.telemetry_field_quality(p_quality jsonb) RETURNS jsonb
        LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
        SELECT coalesce(jsonb_object_agg(key,jsonb_build_object(
          'state',CASE WHEN value->>'state' IN ('valid','null','absent','invalid')
                       THEN value->>'state' ELSE 'unknown' END,
          'valid',coalesce(value->'valid'='true'::jsonb,false),
          'eligible',coalesce(value->'eligible'='true'::jsonb,false),
          'reason',CASE WHEN value->>'reason' IN
            ('valid','null','absent','invalid_type','non_finite','non_integer','out_of_range',
             'future_timestamp','payload_conflict','sensor_failed','unknown')
             THEN value->>'reason' ELSE 'unknown' END,
          'reported_unit',CASE WHEN value->>'reported_unit' IN
            ('m/s','deg','s','mb','degC','%','lx','index','W/m2','mm','1','km','min')
             THEN value->>'reported_unit' ELSE NULL END,
          'canonical_unit',CASE WHEN value->>'canonical_unit' IN
            ('m/s','deg','s','hPa','degC','%','lx','index','W/m2','mm','1','km','min')
             THEN value->>'canonical_unit' ELSE NULL END,
          'range_policy',CASE WHEN value->>'range_policy'='v171-basic-domains-1'
                             THEN 'v171-basic-domains-1' ELSE 'unknown' END,
          'support',jsonb_build_object(
            'kind',CASE WHEN value#>>'{{support,kind}}' IN
              ('preceding_minute','reported_interval','instantaneous','unknown')
              THEN value#>>'{{support,kind}}' ELSE 'unknown' END,
            'qualified',coalesce(value#>'{{support,qualified}}'='true'::jsonb,false)
                        AND coalesce(value->'eligible'='true'::jsonb,false),
            'reported_seconds',CASE
              WHEN jsonb_typeof(value#>'{{support,reported_seconds}}')='number'
              THEN CASE WHEN (value#>>'{{support,reported_seconds}}')::numeric BETWEEN 0 AND 86400
                THEN value#>'{{support,reported_seconds}}' ELSE NULL END ELSE NULL END,
            'policy',CASE WHEN value#>>'{{support,policy}}' IN
              ('v171-rain-firmware-unqualified-1','v171-legacy-rain-support-unqualified-1',
               'v171-wind-report-interval-1','requires-source-time-hold-policy')
              THEN value#>>'{{support,policy}}' ELSE 'unknown' END
          ))),'{{}}'::jsonb)
        FROM jsonb_each(CASE WHEN jsonb_typeof(p_quality->'fields')='object'
                     THEN p_quality->'fields' ELSE '{{}}'::jsonb END)
        WHERE key IN ({metrics}) $$""",
        ]
    )
    public_families = [name for name in FAMILIES if name not in {"hub_status", "device_status"}]
    selects = [
        f"""SELECT o.id,s.alias AS station_alias,d.alias AS device_alias,
      o.message_type,o.source_ts AS observed_at,r.received_at,o.row_idx,
      public_api.telemetry_metric_values(o."values") AS "values",
      public_api.telemetry_field_quality(o.quality) AS field_quality,
      public_api.public_metric_quality(o.quality) AS quality
      FROM weather.{family} o JOIN weather.device d ON d.id=o.device_id
      JOIN weather.station s ON s.id=d.station_id
      JOIN weather.telemetry_receipt r ON r.id=o.receipt_id"""
        for family in public_families
    ]
    result.append(
        "CREATE VIEW public_api.telemetry_observations WITH(security_barrier=true) AS "
        + " UNION ALL ".join(selects)
    )
    result.extend(
        [
            "REVOKE ALL ON FUNCTION public_api.telemetry_metric_values(jsonb) FROM PUBLIC",
            "REVOKE ALL ON FUNCTION public_api.telemetry_field_quality(jsonb) FROM PUBLIC",
            "GRANT EXECUTE ON FUNCTION public_api.telemetry_metric_values(jsonb) "
            "TO weather_api,weather_worker",
            "GRANT EXECUTE ON FUNCTION public_api.telemetry_field_quality(jsonb) "
            "TO weather_api,weather_worker",
            "GRANT SELECT ON public_api.telemetry_observations TO weather_api,weather_worker",
        ]
    )
    for name in TABLE_NAMES:
        result.append(f"GRANT SELECT,INSERT ON weather.{name} TO weather_ingest")
    result.extend(
        [
            "GRANT SELECT ON weather.telemetry_coverage_minute TO weather_ingest,weather_worker",
            "REVOKE ALL ON FUNCTION weather.qualify_telemetry_conflict(uuid) FROM PUBLIC",
            "GRANT EXECUTE ON FUNCTION weather.qualify_telemetry_conflict(uuid) TO weather_ingest",
            "REVOKE ALL ON FUNCTION weather.mark_strike_multiplicity(uuid) FROM PUBLIC",
            "GRANT EXECUTE ON FUNCTION weather.mark_strike_multiplicity(uuid) TO weather_ingest",
        ]
    )
    return result
