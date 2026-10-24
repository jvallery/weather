# SPDX-License-Identifier: MIT
"""Immutable v1 functions, views and grants; consumed by migration 0001."""

TELEMETRY_FAMILIES = (
    "obs_st",
    "rapid_wind",
    "evt_precip",
    "evt_strike",
    "device_status",
    "hub_status",
    "obs_air",
    "obs_sky",
)
PUBLIC_FAMILIES = tuple(
    x for x in TELEMETRY_FAMILIES if x not in {"device_status", "hub_status"}
)
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


def statements(table_names: list[str]) -> list[str]:
    """Each list item is one PostgreSQL statement (function bodies may contain semicolons)."""
    metrics = ",".join("'" + key + "'" for key in PUBLIC_METRIC_KEYS)
    result = [
        """INSERT INTO weather.camera(id,label) VALUES
           ('skycam_east','East sky'),('skycam_west','West sky'),('frontyard_1','Front yard')
           ON CONFLICT (id) DO NOTHING""",
        """CREATE FUNCTION weather.ensure_days(p_from date, p_to date, p_site text DEFAULT 'local')
           RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,weather AS $$
           BEGIN
             IF p_from IS NULL OR p_to IS NULL OR p_to < p_from OR p_site IS NULL
                OR length(p_site) NOT BETWEEN 1 AND 64 THEN
               RAISE EXCEPTION 'invalid day range' USING ERRCODE='22023';
             END IF;
             INSERT INTO weather.weather_day(site,local_date,timezone,starts_at,ends_at)
             SELECT p_site,d::date,'America/Denver',
                    d::timestamp AT TIME ZONE 'America/Denver',
                    (d::date+1)::timestamp AT TIME ZONE 'America/Denver'
             FROM generate_series(p_from::timestamp,p_to::timestamp,interval '1 day') AS s(d)
             ON CONFLICT (site,local_date) DO NOTHING;
             INSERT INTO weather.archive_hour_slot
                 (weather_day_id,site,starts_at,ends_at,local_label,utc_offset_minutes,fold)
             SELECT id,site,h,h+interval '1 hour',
                    to_char(h AT TIME ZONE 'America/Denver','HH24:MI'),
                    (extract(epoch FROM ((h AT TIME ZONE 'America/Denver')-
                                         (h AT TIME ZONE 'UTC')))/60)::integer,
                    (row_number() OVER
                      (PARTITION BY id,to_char(h AT TIME ZONE 'America/Denver','HH24:MI')
                       ORDER BY h)-1)::integer
             FROM weather.weather_day
             CROSS JOIN LATERAL generate_series(starts_at,ends_at-interval '1 hour',
                                                interval '1 hour') AS slots(h)
             WHERE site=p_site AND local_date BETWEEN p_from AND p_to
             ON CONFLICT (site,starts_at) DO NOTHING;
           END $$""",
        """CREATE FUNCTION weather.reject_editorial_mutation() RETURNS trigger
           LANGUAGE plpgsql SET search_path=pg_catalog AS $$
           BEGIN RAISE EXCEPTION 'editorial revisions are append only' USING ERRCODE='55000'; END
           $$""",
        """CREATE TRIGGER editorial_revision_append_only BEFORE UPDATE OR DELETE
           ON weather.editorial_revision FOR EACH ROW
           EXECUTE FUNCTION weather.reject_editorial_mutation()""",
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
        """CREATE FUNCTION public_api.append_editorial_revision
           (p_target text,p_expected bigint,p_changes jsonb,p_actor text,p_reason text)
           RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER
           SET search_path=pg_catalog,weather AS $$
           DECLARE new_revision bigint;
           BEGIN
             IF NOT EXISTS(SELECT 1 FROM weather.operator_allowlist WHERE subject=p_actor) THEN
               RAISE EXCEPTION 'operator not allowed' USING ERRCODE='42501';
             END IF;
             IF p_expected IS NULL OR p_expected < 0 OR p_target IS NULL
                OR length(p_target) NOT BETWEEN 1 AND 256
                OR p_changes IS NULL OR jsonb_typeof(p_changes)<>'object'
                OR octet_length(p_changes::text)>65536
                OR p_reason IS NULL OR length(p_reason) NOT BETWEEN 1 AND 2000 THEN
               RAISE EXCEPTION 'invalid editorial request' USING ERRCODE='22023';
             END IF;
             INSERT INTO weather.editorial_target(target) VALUES(p_target)
               ON CONFLICT(target) DO NOTHING;
             UPDATE weather.editorial_target SET current_revision=current_revision+1
               WHERE target=p_target AND current_revision=p_expected
               RETURNING current_revision INTO new_revision;
             IF NOT FOUND THEN
               RAISE EXCEPTION 'revision conflict' USING ERRCODE='40001';
             END IF;
             INSERT INTO weather.editorial_revision(target,revision_no,actor,changes,reason)
               VALUES(p_target,new_revision,p_actor,p_changes,p_reason);
             INSERT INTO weather.outbox(topic,idempotency_key,payload,expected_revision,
                                       not_before,deadline)
               VALUES('editorial.changed','editorial:'||p_target||':'||new_revision,
                      jsonb_build_object('target',p_target,'revision',new_revision),new_revision,
                      clock_timestamp(),clock_timestamp()+interval '1 day');
             RETURN new_revision;
           END $$""",
        f"""CREATE FUNCTION public_api.public_metric_values(p_values jsonb) RETURNS jsonb
            LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
            SELECT coalesce(jsonb_object_agg(key,value),'{{}}'::jsonb)
            FROM jsonb_each(p_values) WHERE key IN ({metrics})
              AND jsonb_typeof(value) IN ('number','null') $$""",
        """CREATE FUNCTION public_api.public_metric_quality(p_quality jsonb) RETURNS jsonb
           LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
           SELECT jsonb_build_object('valid',coalesce(p_quality->'valid'='true'::jsonb,false),
             'reason',CASE WHEN p_quality->>'reason' IN
               ('valid','missing','invalid','suspect','unknown','source_clock_invalid',
                'source_time_missing','stale','unsupported','out_of_range','duplicate')
               THEN p_quality->>'reason' ELSE 'unknown' END) $$""",
        """CREATE VIEW public_api.stations WITH (security_barrier=true) AS SELECT id,alias,site FROM weather.station""",
        """CREATE VIEW public_api.devices WITH (security_barrier=true) AS SELECT d.id,d.alias,s.alias AS station_alias,
           d.valid_from,d.valid_until FROM weather.device d
           JOIN weather.station s ON s.id=d.station_id""",
        """CREATE VIEW public_api.weather_days WITH (security_barrier=true) AS
           SELECT id,site,local_date,timezone,starts_at,ends_at FROM weather.weather_day""",
        """CREATE VIEW public_api.hour_slots WITH (security_barrier=true) AS
           SELECT id,weather_day_id,site,starts_at,ends_at,local_label,utc_offset_minutes,fold
           FROM weather.archive_hour_slot""",
        """CREATE VIEW public_api.cameras WITH (security_barrier=true) AS SELECT id,label FROM weather.camera""",
        """CREATE VIEW public_api.assets WITH (security_barrier=true) AS
           SELECT id,class,sha256,extension,bytes,content_type,width,height,
                  public_not_before,created_at
           FROM weather.media_asset WHERE class<>'original' AND bucket='weather-media'
             AND visibility='public' AND state='available' AND NOT withdrawn
             AND public_not_before <= clock_timestamp()""",
        """CREATE VIEW public_api.segments WITH (security_barrier=true) AS
           SELECT s.id,s.camera_id,s.kind,s.starts_at,s.ends_at,s.frames,s.revision,s.state,
                  c.cycle_date FROM weather.segment s
           JOIN weather.solar_cycle c ON c.id=s.solar_cycle_id
           WHERE s.state IN ('published','partial','unavailable')""",
        """CREATE VIEW public_api.videos WITH (security_barrier=true) AS
           SELECT v.id,v.segment_id,s.camera_id,s.kind,s.starts_at,s.ends_at,s.frames,
                  v.revision,v.state,v.time_map,v.quality,p.generation,p.committed_at,
                  CASE WHEN v.state IN ('published','partial') THEN v.native_asset_id END
                    AS native_asset_id,
                  CASE WHEN v.state IN ('published','partial') THEN v.web_asset_id END
                    AS web_asset_id,
                  CASE WHEN v.state IN ('published','partial') THEN v.mobile_asset_id END
                    AS mobile_asset_id,
                  CASE WHEN v.state IN ('published','partial') THEN v.poster_asset_id END
                    AS poster_asset_id,
                  CASE WHEN v.state IN ('published','partial') THEN v.sidecar_asset_id END
                    AS sidecar_asset_id
           FROM weather.video v JOIN weather.segment s ON s.id=v.segment_id
           JOIN weather.publication p ON p.video_id=v.id AND p.revision=v.revision
           WHERE p.current AND p.public_not_before<=clock_timestamp() AND NOT v.withdrawn
             AND v.state IN ('published','partial','unavailable','expired')
             AND (v.state IN ('unavailable','expired') OR
                (EXISTS(SELECT 1 FROM public_api.assets a WHERE a.id=v.native_asset_id)
                 AND EXISTS(SELECT 1 FROM public_api.assets a WHERE a.id=v.web_asset_id)
                 AND EXISTS(SELECT 1 FROM public_api.assets a WHERE a.id=v.mobile_asset_id)
                 AND EXISTS(SELECT 1 FROM public_api.assets a WHERE a.id=v.poster_asset_id)
                 AND EXISTS(SELECT 1 FROM public_api.assets a WHERE a.id=v.sidecar_asset_id)))""",
        """CREATE VIEW public_api.keograms WITH (security_barrier=true) AS
           SELECT k.id,k.segment_id,k.asset_id,k.revision,k.utc_bin_map,k.state
           FROM weather.keogram k JOIN public_api.assets a ON a.id=k.asset_id
           JOIN public_api.segments s ON s.id=k.segment_id
           WHERE k.state IN ('published','partial')""",
        """CREATE VIEW public_api.stills WITH (security_barrier=true) AS
           SELECT d.id,s.camera_id,s.weather_day_id,s.hour_slot_id,s.source_segment_id,
                  s.source_ts,s.uncertainty_seconds,s.revision,d.asset_id,d.recipe_revision
           FROM weather.still_derivative d
           JOIN weather.archival_still s ON s.id=d.archival_still_id
           JOIN public_api.assets a ON a.id=d.asset_id
           WHERE d.state='published' AND d.committed_at IS NOT NULL AND s.state='active'""",
        """CREATE VIEW public_api.previews WITH (security_barrier=true) AS
           SELECT id,camera_id,generation,bytes,width,height,captured_lower,captured_upper,
                  public_not_before,expires_at,first_published_at
           FROM weather.camera_preview WHERE timing_trusted
             AND public_not_before<=clock_timestamp() AND expires_at>clock_timestamp()
             AND first_published_at IS NOT NULL""",
        """CREATE VIEW public_api.events WITH (security_barrier=true) AS
           SELECT id,weather_day_id,starts_at,ends_at,origin,basis,revision
           FROM weather.weather_event WHERE published AND NOT withdrawn""",
        "REVOKE ALL ON SCHEMA weather,public_api FROM PUBLIC",
        "REVOKE ALL ON ALL TABLES IN SCHEMA weather,public_api FROM PUBLIC",
        "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA weather,public_api FROM PUBLIC",
        "GRANT USAGE ON SCHEMA weather TO weather_ingest,weather_worker",
        "GRANT USAGE ON SCHEMA public_api TO weather_api",
    ]
    unions = [
        f"""SELECT o.id,s.alias AS station_alias,d.alias AS device_alias,o.message_type,
            o.source_ts AS observed_at,r.received_at,o.row_idx,
            public_api.public_metric_values(o."values") AS "values",
            public_api.public_metric_quality(o.quality) AS quality
            FROM weather.{family} o JOIN weather.device d ON d.id=o.device_id
            JOIN weather.station s ON s.id=d.station_id
            JOIN weather.telemetry_receipt r ON r.id=o.receipt_id"""
        for family in PUBLIC_FAMILIES
    ]
    # Create the union before granting views; status families and private IDs are absent.
    result.insert(
        -5,
        "CREATE VIEW public_api.observations WITH (security_barrier=true) AS "
        + " UNION ALL ".join(unions),
    )
    telemetry = {"station", "device", "telemetry_receipt", *TELEMETRY_FAMILIES}
    for name in sorted(telemetry):
        result.append(f"GRANT SELECT,INSERT ON weather.{name} TO weather_ingest")
        result.append(f"GRANT SELECT ON weather.{name} TO weather_worker")
    for name in sorted(
        set(table_names)
        - telemetry
        - {"operator_allowlist", "editorial_revision", "editorial_target"}
    ):
        result.append(
            f"GRANT SELECT,INSERT,UPDATE,DELETE ON weather.{name} TO weather_worker"
        )
    result.extend(
        [
            "GRANT SELECT ON weather.editorial_revision,weather.editorial_target TO weather_worker",
            "GRANT EXECUTE ON FUNCTION weather.ensure_days(date,date,text) TO weather_worker",
            "GRANT SELECT ON ALL TABLES IN SCHEMA public_api TO weather_api",
            "GRANT EXECUTE ON FUNCTION public_api.public_metric_values(jsonb),"
            "public_api.public_metric_quality(jsonb) TO weather_api",
            "GRANT EXECUTE ON FUNCTION public_api.append_editorial_revision"
            "(text,bigint,jsonb,text,text) TO weather_api,weather_worker",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA weather REVOKE ALL ON TABLES FROM PUBLIC",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA weather REVOKE ALL ON FUNCTIONS FROM PUBLIC",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public_api REVOKE ALL ON TABLES FROM PUBLIC",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public_api REVOKE ALL ON FUNCTIONS FROM PUBLIC",
        ]
    )
    return result
