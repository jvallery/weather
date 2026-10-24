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
        /** Get Aggregates */
        readonly get: operations["getAggregates"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/weather/almanac": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Get Almanac */
        readonly get: operations["getAlmanac"];
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
        /** Get Current */
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
        /** Get Metrics */
        readonly get: operations["getMetrics"];
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
        /** Get Observations */
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
            readonly coverage: components["schemas"]["Coverage"];
            /** Direction State */
            readonly direction_state: ("directional" | "variable" | "calm" | "missing") | null;
            /**
             * End
             * Format: date-time
             */
            readonly end: string;
            /** Evidence Count */
            readonly evidence_count: number;
            /** Evidence Hash */
            readonly evidence_hash: string | null;
            /** Metric */
            readonly metric: string;
            /** Reason */
            readonly reason: string | null;
            /** Rose */
            readonly rose: readonly components["schemas"]["RoseBin"][] | null;
            /**
             * Start
             * Format: date-time
             */
            readonly start: string;
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
             * End
             * Format: date-time
             */
            readonly end: string;
            /**
             * Generated At
             * Format: date-time
             */
            readonly generated_at: string;
            /** Items */
            readonly items: readonly components["schemas"]["Aggregate"][];
            /** Resolution */
            readonly resolution: string;
            /**
             * Start
             * Format: date-time
             */
            readonly start: string;
        };
        /** Almanac */
        readonly Almanac: {
            readonly comparison_interval: components["schemas"]["Interval"] | null;
            readonly coverage: components["schemas"]["Coverage"] | null;
            /** Derived */
            readonly derived: boolean;
            /** Evidence Count */
            readonly evidence_count: number;
            /** Evidence Hash */
            readonly evidence_hash: string | null;
            /** Formula Revision */
            readonly formula_revision: string;
            /**
             * Generated At
             * Format: date-time
             */
            readonly generated_at: string;
            readonly history_interval: components["schemas"]["Interval"] | null;
            readonly interval: components["schemas"]["Interval"];
            /** Items */
            readonly items: readonly (components["schemas"]["RecordItem"] | components["schemas"]["RainDayItem"] | components["schemas"]["WindRoseBinItem"] | components["schemas"]["AstronomyItem"] | components["schemas"]["ComparisonItem"])[];
            /** Label */
            readonly label: string;
            /** Library Revision */
            readonly library_revision: string;
            /** Metric */
            readonly metric: string | null;
            /** Missing Reasons */
            readonly missing_reasons: readonly ("no_observations" | "partial_coverage" | "below_record_coverage" | "before_history" | "inadequate_coverage" | "site_not_configured" | "solar_events_unavailable" | "no_moonrise" | "no_moonset")[];
            /** Next Cursor */
            readonly next_cursor: string | null;
            /**
             * Product
             * @enum {string}
             */
            readonly product: "record" | "rain_day" | "wind_rose_bin" | "astronomy" | "comparison";
            /** Statistic */
            readonly statistic: string | null;
            /**
             * Timezone
             * @constant
             */
            readonly timezone: "America/Denver";
            /** Unit */
            readonly unit: string | null;
        };
        /**
         * AstronomyItem
         * @description Derived sun and moon events for one local day from the private site; no coordinates.
         */
        readonly AstronomyItem: {
            /** Civil Dawn */
            readonly civil_dawn: string | null;
            /** Civil Dusk */
            readonly civil_dusk: string | null;
            /** Day Length Seconds */
            readonly day_length_seconds: number | null;
            /**
             * End
             * Format: date-time
             */
            readonly end: string;
            /**
             * Local Date
             * Format: date
             */
            readonly local_date: string;
            /** Missing Reasons */
            readonly missing_reasons: readonly ("no_observations" | "partial_coverage" | "below_record_coverage" | "before_history" | "inadequate_coverage" | "site_not_configured" | "solar_events_unavailable" | "no_moonrise" | "no_moonset")[];
            /** Moon Phase */
            readonly moon_phase: number | null;
            /** Moon Phase Name */
            readonly moon_phase_name: ("new" | "waxing_crescent" | "first_quarter" | "waxing_gibbous" | "full" | "waning_gibbous" | "last_quarter" | "waning_crescent") | null;
            /** Moonrise */
            readonly moonrise: string | null;
            /** Moonset */
            readonly moonset: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            readonly product: "astronomy";
            /** Solar Noon */
            readonly solar_noon: string | null;
            /**
             * Start
             * Format: date-time
             */
            readonly start: string;
            /** Sunrise */
            readonly sunrise: string | null;
            /** Sunset */
            readonly sunset: string | null;
        };
        /** ComparisonItem */
        readonly ComparisonItem: {
            /** Difference */
            readonly difference: number | null;
            readonly first: components["schemas"]["ComparisonWindow"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            readonly product: "comparison";
            /** Reason */
            readonly reason: ("no_observations" | "partial_coverage" | "below_record_coverage" | "before_history" | "inadequate_coverage" | "site_not_configured" | "solar_events_unavailable" | "no_moonrise" | "no_moonset") | null;
            readonly second: components["schemas"]["ComparisonWindow"];
        };
        /** ComparisonWindow */
        readonly ComparisonWindow: {
            /** Adequate */
            readonly adequate: boolean;
            readonly coverage: components["schemas"]["Coverage"];
            /** Duration Seconds */
            readonly duration_seconds: number;
            /**
             * End
             * Format: date-time
             */
            readonly end: string;
            /** Evidence Count */
            readonly evidence_count: number;
            /** Evidence Hash */
            readonly evidence_hash: string | null;
            /** Missing Days */
            readonly missing_days: readonly string[];
            /** Missing Reasons */
            readonly missing_reasons: readonly ("no_observations" | "partial_coverage" | "below_record_coverage" | "before_history" | "inadequate_coverage" | "site_not_configured" | "solar_events_unavailable" | "no_moonrise" | "no_moonset")[];
            /** Partial Days */
            readonly partial_days: readonly string[];
            /**
             * Start
             * Format: date-time
             */
            readonly start: string;
            /** Value */
            readonly value: number | null;
        };
        /**
         * Conversion
         * @description display = canonical * scale_numerator / scale_denominator + offset (as a ratio).
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
            /** Expected Seconds */
            readonly expected_seconds: number;
            /** Fraction */
            readonly fraction: number;
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
        /**
         * Interval
         * @description Half-open UTC interval [start, end).
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
        /** Metric */
        readonly Metric: {
            /** Coverage */
            readonly coverage: number | null;
            /** Expected Interval Seconds */
            readonly expected_interval_seconds: number | null;
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
            readonly quality: "valid" | "missing" | "invalid";
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
            /** Expected Interval Seconds */
            readonly expected_interval_seconds: number | null;
            /** Label */
            readonly label: string;
            /** Metric */
            readonly metric: string;
            /** Statistics */
            readonly statistics: readonly string[];
        };
        /** MetricRegistry */
        readonly MetricRegistry: {
            /** Metrics */
            readonly metrics: readonly components["schemas"]["MetricDefinition"][];
        };
        /** ObservationPoint */
        readonly ObservationPoint: {
            /** Id */
            readonly id: string;
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
            readonly quality: "valid" | "missing" | "invalid";
            /**
             * Received At
             * Format: date-time
             */
            readonly received_at: string;
            /** Unit */
            readonly unit: string;
            /** Value */
            readonly value: number | null;
        };
        /** Observations */
        readonly Observations: {
            /**
             * End
             * Format: date-time
             */
            readonly end: string;
            /**
             * Generated At
             * Format: date-time
             */
            readonly generated_at: string;
            /** Metrics */
            readonly metrics: readonly string[];
            /** Next Cursor */
            readonly next_cursor: string | null;
            /** Points */
            readonly points: readonly components["schemas"]["ObservationPoint"][];
            /**
             * Start
             * Format: date-time
             */
            readonly start: string;
        };
        /** Problem */
        readonly Problem: {
            /** Code */
            readonly code: string;
            /** Message */
            readonly message: string;
            /** Suggested Resolution Seconds */
            readonly suggested_resolution_seconds: number | null;
        };
        /**
         * RainDayItem
         * @description Observed accumulation for one local day, never scaled up for missing time.
         */
        readonly RainDayItem: {
            /** Complete */
            readonly complete: boolean;
            readonly coverage: components["schemas"]["Coverage"];
            /**
             * End
             * Format: date-time
             */
            readonly end: string;
            /** Evidence Count */
            readonly evidence_count: number;
            /** Evidence Hash */
            readonly evidence_hash: string | null;
            /**
             * Local Date
             * Format: date
             */
            readonly local_date: string;
            /** Missing Reasons */
            readonly missing_reasons: readonly ("no_observations" | "partial_coverage" | "below_record_coverage" | "before_history" | "inadequate_coverage" | "site_not_configured" | "solar_events_unavailable" | "no_moonrise" | "no_moonset")[];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            readonly product: "rain_day";
            /**
             * Start
             * Format: date-time
             */
            readonly start: string;
            /** Value */
            readonly value: number | null;
        };
        /**
         * RecordItem
         * @description One local day; ranked only with at least 95% metric-duration coverage.
         */
        readonly RecordItem: {
            readonly coverage: components["schemas"]["Coverage"];
            /**
             * End
             * Format: date-time
             */
            readonly end: string;
            /** Evidence Count */
            readonly evidence_count: number;
            /** Evidence Hash */
            readonly evidence_hash: string | null;
            /**
             * Local Date
             * Format: date
             */
            readonly local_date: string;
            /** Missing Reasons */
            readonly missing_reasons: readonly ("no_observations" | "partial_coverage" | "below_record_coverage" | "before_history" | "inadequate_coverage" | "site_not_configured" | "solar_events_unavailable" | "no_moonrise" | "no_moonset")[];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            readonly product: "record";
            /** Rank */
            readonly rank: number | null;
            /** Ranking Eligible */
            readonly ranking_eligible: boolean;
            /**
             * Start
             * Format: date-time
             */
            readonly start: string;
            /** Value */
            readonly value: number | null;
        };
        /**
         * RoseBin
         * @description Duration in one category; directional sectors are half-open, from-direction degrees.
         */
        readonly RoseBin: {
            /**
             * Category
             * @enum {string}
             */
            readonly category: "directional" | "calm" | "variable" | "missing";
            /** Duration Seconds */
            readonly duration_seconds: number;
            /** Sector End */
            readonly sector_end: number | null;
            /** Sector Start */
            readonly sector_start: number | null;
            /** Speed Max */
            readonly speed_max: number | null;
            /** Speed Min */
            readonly speed_min: number | null;
        };
        /**
         * WindRoseBinItem
         * @description Duration in one category over the whole interval; frequency is duration / denominator.
         */
        readonly WindRoseBinItem: {
            /**
             * Category
             * @enum {string}
             */
            readonly category: "directional" | "calm" | "variable" | "missing";
            /** Denominator Seconds */
            readonly denominator_seconds: number;
            /** Duration Seconds */
            readonly duration_seconds: number;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            readonly product: "wind_rose_bin";
            /** Sector End */
            readonly sector_end: number | null;
            /** Sector Start */
            readonly sector_start: number | null;
            /** Speed Max */
            readonly speed_max: number | null;
            /** Speed Min */
            readonly speed_min: number | null;
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
                readonly end: string;
                readonly metrics: readonly string[];
                readonly resolution: string;
                readonly start: string;
                readonly statistics: readonly string[];
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
            /** @description Invalid request. */
            readonly 400: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Weather data is temporarily unavailable. */
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
    readonly getAlmanac: {
        readonly parameters: {
            readonly query: {
                readonly comparison_end?: string | null;
                readonly comparison_start?: string | null;
                readonly cursor?: string | null;
                readonly end: string;
                readonly limit?: number;
                readonly metric?: string | null;
                readonly product: "record" | "rain_day" | "wind_rose_bin" | "astronomy" | "comparison";
                readonly start: string;
                readonly statistic?: string | null;
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
                    readonly "application/json": components["schemas"]["Almanac"];
                };
            };
            /** @description Invalid request. */
            readonly 400: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Weather data is temporarily unavailable. */
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
            readonly header?: {
                readonly "if-none-match"?: string | null;
            };
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
            /** @description Unchanged since the given ETag. */
            readonly 304: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Invalid request. */
            readonly 400: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Weather data is temporarily unavailable. */
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
    readonly getMetrics: {
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
                    readonly "application/json": components["schemas"]["Observations"];
                };
            };
            /** @description Invalid request. */
            readonly 400: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Weather data is temporarily unavailable. */
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
