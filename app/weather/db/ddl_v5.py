# SPDX-License-Identifier: MIT
"""Keep earlier computed revisions and expose only the public scalar projection."""


def statements() -> list[str]:
    return [
        """CREATE FUNCTION weather.reject_aggregate_revision_mutation() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
          RAISE EXCEPTION 'aggregate revisions are append only' USING ERRCODE='55000';
        END $$""",
        """CREATE TRIGGER aggregate_revision_append_only BEFORE UPDATE OR DELETE
        ON weather.aggregate_revision FOR EACH ROW
        EXECUTE FUNCTION weather.reject_aggregate_revision_mutation()""",
        "REVOKE ALL ON FUNCTION weather.reject_aggregate_revision_mutation() FROM PUBLIC",
        "GRANT SELECT,INSERT ON weather.aggregate_revision TO weather_worker",
        "GRANT SELECT,INSERT,UPDATE ON weather.aggregate_head TO weather_worker",
        """CREATE VIEW public_api.aggregate_revisions WITH(security_barrier=true) AS
        SELECT id,scope_key,station_alias,starts_at,ends_at,metric,statistic,policy_revision,
          watermark,created_at,jsonb_build_object(
            'metric',metric,'statistic',statistic,'value',document->'value',
            'unit',CASE statistic WHEN 'solar_energy' THEN 'Wh/m2' WHEN 'trend' THEN 'hPa/h'
              ELSE (SELECT canonical_unit FROM weather.metric_binding b
                    WHERE b.metric=r.metric LIMIT 1) END,
            'interval',jsonb_build_object(
              'start',to_char(starts_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
              'end',to_char(ends_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')),
            'coverage',jsonb_build_object(
              'fraction',document#>'{coverage,fraction}',
              'support_seconds',document#>'{coverage,support_seconds}',
              'expected_seconds',document#>'{coverage,expected_seconds}',
              'algorithm_revision',document#>'{coverage,algorithm_revision}',
              'gaps',coalesce((SELECT jsonb_agg(jsonb_build_object(
                'start',g->'start','end',g->'end'))
                FROM jsonb_array_elements(CASE
                  WHEN jsonb_typeof(document#>'{coverage,gaps}')='array'
                  THEN document#>'{coverage,gaps}' ELSE '[]'::jsonb END) g),'[]'::jsonb)),
            'evidence_ids',document->'evidence_ids',
            'evidence_revision',document->'evidence_revision',
            'algorithm_revision',document->'algorithm_revision',
            'reason',CASE WHEN document->'reason'='null'::jsonb THEN NULL
              WHEN document->>'reason' IN ('no_qualified_support',
                'unknown_or_missing_accumulation_support','boundary_uncertain',
                'no_interval_authority','insufficient_endpoint_support',
                'Wind direction is unavailable.','Wind direction is calm.',
                'Wind direction is variable.','Estimated using uniform intensity over support.')
              THEN document->>'reason' ELSE 'Aggregate source is unavailable.' END
          ) AS document
        FROM weather.aggregate_revision r""",
        "GRANT SELECT ON public_api.aggregate_revisions TO weather_api,weather_worker",
    ]
