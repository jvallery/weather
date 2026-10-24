// SPDX-License-Identifier: MIT
// Generated from code-first Pydantic OpenAPI; run scripts/ci.sh contracts --write.
export interface paths {
    readonly "/api/v1/weather/aggregates": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Aggregates */
        readonly get: operations["getAggregates"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/weather/current": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Current */
        readonly get: operations["getCurrent"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/weather/metrics": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Get Metric Registry */
        readonly get: operations["getMetricRegistry"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/weather/observations": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Observations */
        readonly get: operations["getObservations"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** Aggregate */
        readonly Aggregate: {
            /** Algorithm Revision */
            readonly algorithm_revision: string;
            readonly coverage: components["schemas"]["Coverage"];
            /** Evidence Ids */
            readonly evidence_ids: readonly string[];
            /** Evidence Revision */
            readonly evidence_revision: string;
            readonly interval: components["schemas"]["Interval"];
            /** Metric */
            readonly metric: string;
            /** Reason */
            readonly reason: string | null;
            /** Statistic */
            readonly statistic: string;
            /** Unit */
            readonly unit: string;
            /** Value */
            readonly value: number | null;
        };
        /** Aggregates */
        readonly Aggregates: {
            /**
             * Generated At
             * Format: date-time
             */
            readonly generated_at: string;
            readonly interval: components["schemas"]["Interval"];
            /** Items */
            readonly items: readonly components["schemas"]["Aggregate"][];
            /** Next Cursor */
            readonly next_cursor: string | null;
            /** Resolution Seconds */
            readonly resolution_seconds: number;
            /** Revision */
            readonly revision: string;
            /** Statistic */
            readonly statistic: string;
        };
        /**
         * Conversion
         * @description display = canonical * scale_numerator / scale_denominator + offset ratio.
         */
        readonly Conversion: {
            /** Offset Denominator */
            readonly offset_denominator: number;
            /** Offset Numerator */
            readonly offset_numerator: number;
            /** Scale Denominator */
            readonly scale_denominator: number;
            /** Scale Numerator */
            readonly scale_numerator: number;
            /** Unit */
            readonly unit: string;
        };
        /** Coverage */
        readonly Coverage: {
            /** Algorithm Revision */
            readonly algorithm_revision: string;
            /** Expected Seconds */
            readonly expected_seconds: number;
            /** Fraction */
            readonly fraction: number;
            /** Gaps */
            readonly gaps: readonly components["schemas"]["Interval"][];
            /** Support Seconds */
            readonly support_seconds: number;
        };
        /** Current */
        readonly Current: {
            /**
             * Data State
             * @enum {string}
             */
            readonly data_state: "live" | "cached" | "unavailable";
            /**
             * Generated At
             * Format: date-time
             */
            readonly generated_at: string;
            /** Metrics */
            readonly metrics: readonly components["schemas"]["Metric"][];
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            readonly schema_version: 1;
            /** Station Alias */
            readonly station_alias: string;
        };
        /** History */
        readonly History: {
            /**
             * Aggregation
             * @default observed
             * @constant
             */
            readonly aggregation: "observed";
            /** Gaps */
            readonly gaps: readonly components["schemas"]["Interval"][];
            /**
             * Generated At
             * Format: date-time
             */
            readonly generated_at: string;
            readonly interval: components["schemas"]["Interval"];
            /** Next Cursor */
            readonly next_cursor: string | null;
            /** Points */
            readonly points: readonly components["schemas"]["HistoryPoint"][];
            /** Resolution Seconds */
            readonly resolution_seconds: number;
        };
        /** HistoryPoint */
        readonly HistoryPoint: {
            /** Evidence Id */
            readonly evidence_id: string;
            /** Metric */
            readonly metric: string;
            /**
             * Observed At
             * Format: date-time
             */
            readonly observed_at: string;
            /**
             * Quality
             * @enum {string}
             */
            readonly quality: "valid" | "suspect" | "missing" | "invalid";
            /** Reason */
            readonly reason: string | null;
            /**
             * Received At
             * Format: date-time
             */
            readonly received_at: string;
            readonly support_interval: components["schemas"]["Interval"] | null;
            /** Unit */
            readonly unit: string;
            /** Value */
            readonly value: number | null;
        };
        /**
         * Interval
         * @description Inclusive start, exclusive end, preserving microsecond ordering.
         */
        readonly Interval: {
            /**
             * End
             * Format: date-time
             */
            readonly end: string;
            /**
             * Start
             * Format: date-time
             */
            readonly start: string;
        };
        /** Lightning */
        readonly Lightning: {
            /** Aggregate Id */
            readonly aggregate_id: string;
            /** Algorithm Revision */
            readonly algorithm_revision: string;
            /**
             * Authority
             * @enum {string}
             */
            readonly authority: "obs_st" | "obs_air";
            /** Boundary Uncertain Count */
            readonly boundary_uncertain_count: number;
            readonly coverage: components["schemas"]["Coverage"];
            /** Evidence Ids */
            readonly evidence_ids: readonly string[];
            /** Evidence Revision */
            readonly evidence_revision: string;
            /** Observed Subtotal */
            readonly observed_subtotal: number;
            /** Reason */
            readonly reason: string | null;
            /** Value */
            readonly value: number | null;
        };
        /** Metric */
        readonly Metric: {
            /** Expected Interval Seconds */
            readonly expected_interval_seconds: (number) | null;
            /**
             * Freshness
             * @enum {string}
             */
            readonly freshness: "fresh" | "stale" | "unavailable";
            /** Metric */
            readonly metric: string;
            /** Observed At */
            readonly observed_at: string | null;
            /**
             * Quality
             * @enum {string}
             */
            readonly quality: "valid" | "suspect" | "missing" | "invalid";
            /** Reason */
            readonly reason: string | null;
            /** Received At */
            readonly received_at: string | null;
            /** Unit */
            readonly unit: string;
            /** Valid Until */
            readonly valid_until: string | null;
            /** Value */
            readonly value: number | null;
        };
        /** MetricDefinition */
        readonly MetricDefinition: {
            /** Canonical Unit */
            readonly canonical_unit: string;
            /** Conversions */
            readonly conversions: readonly components["schemas"]["Conversion"][];
            /** Display Units */
            readonly display_units: readonly string[];
            /** Expected Interval Seconds */
            readonly expected_interval_seconds: (number) | null;
            /** Label */
            readonly label: string;
            /** Metric */
            readonly metric: string;
            /**
             * Public
             * @default true
             * @constant
             */
            readonly public: true;
            /** Revision */
            readonly revision: string;
            /** Statistics */
            readonly statistics: readonly string[];
        };
        /** MetricRegistry */
        readonly MetricRegistry: {
            /** Metrics */
            readonly metrics: readonly components["schemas"]["MetricDefinition"][];
            /** Revision */
            readonly revision: string;
        };
        /** Problem */
        readonly Problem: {
            /** Code */
            readonly code: string;
            /** Message */
            readonly message: string;
            /** Request Id */
            readonly request_id: string | null;
            /** Suggested Resolution Seconds */
            readonly suggested_resolution_seconds: number | null;
        };
        /**
         * Wind
         * @description Canonical m/s speeds, from-direction degrees and distinct duration coverage.
         */
        readonly Wind: {
            /** Aggregate Id */
            readonly aggregate_id: string;
            /** Algorithm Revision */
            readonly algorithm_revision: string;
            readonly direction_coverage: components["schemas"]["Coverage"];
            /** Direction Degrees */
            readonly direction_degrees: (number) | null;
            /**
             * Direction State
             * @enum {string}
             */
            readonly direction_state: "directional" | "variable" | "calm" | "unavailable";
            /** Evidence Ids */
            readonly evidence_ids: readonly string[];
            /** Evidence Revision */
            readonly evidence_revision: string;
            /** Maximum Gust */
            readonly maximum_gust: (number) | null;
            /** Mean Speed */
            readonly mean_speed: (number) | null;
            /** Resultant */
            readonly resultant: (number) | null;
            /** Rose */
            readonly rose: readonly components["schemas"]["WindRoseBin"][];
            readonly speed_coverage: components["schemas"]["Coverage"];
        };
        /**
         * WindRoseBin
         * @description Angles are meteorological-from degrees; speed bounds are canonical m/s.
         */
        readonly WindRoseBin: {
            /** Angle End */
            readonly angle_end: (number) | null;
            /** Angle Start */
            readonly angle_start: (number) | null;
            /**
             * Category
             * @enum {string}
             */
            readonly category: "directional" | "calm" | "variable" | "missing";
            /** Denominator Seconds */
            readonly denominator_seconds: number;
            /** Duration Seconds */
            readonly duration_seconds: number;
            /** Speed Max */
            readonly speed_max: (number) | null;
            /** Speed Min */
            readonly speed_min: (number) | null;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    readonly getAggregates: {
        readonly parameters: {
            readonly query: {
                readonly cursor?: string | null;
                readonly end: string;
                readonly limit?: number;
                readonly metrics: readonly string[];
                readonly resolution_seconds: number;
                readonly start: string;
                readonly statistic: string;
            };
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["Aggregates"];
                };
            };
            /** @description Invalid bounded public request. */
            readonly 400: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Public data is unavailable. */
            readonly 503: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    readonly getCurrent: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["Current"];
                };
            };
            /** @description Invalid bounded public request. */
            readonly 400: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Public data is unavailable. */
            readonly 503: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    readonly getMetricRegistry: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["MetricRegistry"];
                };
            };
        };
    };
    readonly getObservations: {
        readonly parameters: {
            readonly query: {
                readonly cursor?: string | null;
                readonly end: string;
                readonly limit?: number;
                readonly metrics: readonly string[];
                readonly start: string;
            };
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["History"];
                };
            };
            /** @description Invalid bounded public request. */
            readonly 400: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Public data is unavailable. */
            readonly 503: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
}
