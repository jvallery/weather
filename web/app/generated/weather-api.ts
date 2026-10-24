// SPDX-License-Identifier: MIT
// Generated from code-first Pydantic OpenAPI; run scripts/ci.sh contracts --write.
export interface paths {
    readonly "/api/v1/events": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Get Events */
        readonly get: operations["getEvents"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/events/{event_id}": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Get Event */
        readonly get: operations["getEvent"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/search": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /** Search */
        readonly post: operations["search"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/timelapses": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** List Timelapses */
        readonly get: operations["listTimelapses"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/timelapses/{video_id}": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Get Timelapse */
        readonly get: operations["getTimelapse"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
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
            readonly comparison_interval: components["schemas"]["weather__api__models__Interval"] | null;
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
            readonly history_interval: components["schemas"]["weather__api__models__Interval"] | null;
            readonly interval: components["schemas"]["weather__api__models__Interval"];
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
        /** Asset */
        readonly Asset: {
            /**
             * Asset Id
             * Format: uuid
             */
            readonly asset_id: string;
            /** Bytes */
            readonly bytes: number;
            /**
             * Class
             * @enum {string}
             */
            readonly class: "video" | "poster" | "keogram" | "sidecar";
            /** Height */
            readonly height: number | null;
            /** Mime */
            readonly mime: string;
            /** Url */
            readonly url: string;
            /** Width */
            readonly width: number | null;
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
        /** Event */
        readonly Event: {
            /**
             * Basis
             * @enum {string}
             */
            readonly basis: "instrument" | "visual" | "inference";
            /** Camera */
            readonly camera: ("skycam_east" | "skycam_west" | "frontyard_1") | null;
            readonly coverage: components["schemas"]["Coverage"] | null;
            /** Distance Km */
            readonly distance_km: number | null;
            /** Evidence Ids */
            readonly evidence_ids: readonly string[];
            /** Gust Mps */
            readonly gust_mps: number | null;
            /** Human Edited */
            readonly human_edited: boolean;
            /** Id */
            readonly id: string;
            /**
             * Kind
             * @enum {string}
             */
            readonly kind: "precipitation_onset" | "daily_gust_max" | "lightning_strike" | "lightning_interval" | "visual" | "synthesis" | "manual";
            /** Label */
            readonly label: string;
            /** Local Dates */
            readonly local_dates: readonly string[];
            /**
             * Origin
             * @enum {string}
             */
            readonly origin: "measured" | "visual" | "synthesis" | "manual";
            /**
             * Precision
             * @enum {string}
             */
            readonly precision: "second" | "interval" | "clip";
            /** Revision */
            readonly revision: number;
            /** Rule Revision */
            readonly rule_revision: string;
            readonly source_interval: components["schemas"]["EventInterval"];
            /** Strikes */
            readonly strikes: number | null;
            /**
             * Updated At
             * Format: date-time
             */
            readonly updated_at: string;
            /** Videos */
            readonly videos: readonly components["schemas"]["EventVideo"][];
        };
        /**
         * EventInterval
         * @description Half-open [start, end) source-time interval; never empty.
         */
        readonly EventInterval: {
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
        /** Events */
        readonly Events: {
            /**
             * Generated At
             * Format: date-time
             */
            readonly generated_at: string;
            /** Items */
            readonly items: readonly components["schemas"]["Event"][];
            /** Next Cursor */
            readonly next_cursor: string | null;
        };
        /**
         * EventSeek
         * @description Player seconds for the event's overlap with one published video, via its source map.
         */
        readonly EventSeek: {
            /** End Seconds */
            readonly end_seconds: number;
            readonly source_interval: components["schemas"]["EventInterval"];
            /** Start Seconds */
            readonly start_seconds: number;
        };
        /**
         * EventVideo
         * @description Navigation context only: not a claim that this camera visibly recorded the event.
         */
        readonly EventVideo: {
            /**
             * Camera
             * @enum {string}
             */
            readonly camera: "skycam_east" | "skycam_west" | "frontyard_1";
            /** Cycle Date */
            readonly cycle_date: string | null;
            readonly seek: components["schemas"]["EventSeek"] | null;
            /** Segment */
            readonly segment: ("daytime" | "overnight") | null;
            /**
             * State
             * @enum {string}
             */
            readonly state: "available" | "unavailable";
            /** Video Id */
            readonly video_id: string | null;
        };
        /**
         * MatchedMeasurement
         * @description A whole-video aggregate that satisfied a predicate; never a clip-time event.
         */
        readonly MatchedMeasurement: {
            readonly coverage: components["schemas"]["Coverage"];
            /** Evidence Count */
            readonly evidence_count: number;
            /** Evidence Hash */
            readonly evidence_hash: string | null;
            readonly interval: components["schemas"]["weather__api__models__Interval"];
            /** Metric */
            readonly metric: string;
            /** Statistic */
            readonly statistic: string;
            /** Unit */
            readonly unit: string;
            /** Value */
            readonly value: number;
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
        /** Predicate */
        readonly Predicate: {
            /**
             * Metric
             * @enum {string}
             */
            readonly metric: "precipitation" | "wind_gust" | "temperature";
            /**
             * Min Coverage
             * @default 0.95
             */
            readonly min_coverage: number;
            /**
             * Op
             * @enum {string}
             */
            readonly op: "<" | "<=" | "=" | ">=" | ">";
            /**
             * Statistic
             * @enum {string}
             */
            readonly statistic: "sum" | "max" | "min" | "mean";
            /** Unit */
            readonly unit: string;
            /** Value */
            readonly value: number;
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
        /** Rendition */
        readonly Rendition: {
            readonly asset: components["schemas"]["Asset"];
            /**
             * Role
             * @enum {string}
             */
            readonly role: "native" | "web" | "mobile";
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
        /** SearchHit */
        readonly SearchHit: {
            /** Analysis Mode */
            readonly analysis_mode: string;
            /** Camera Id */
            readonly camera_id: string;
            /** Document Id */
            readonly document_id: string;
            /**
             * Evidence Precision
             * @enum {string}
             */
            readonly evidence_precision: "frame" | "interval" | "clip" | "segment";
            /** Matched Measurements */
            readonly matched_measurements: readonly components["schemas"]["MatchedMeasurement"][];
            /** Media Revision */
            readonly media_revision: number;
            /** Poster Asset Id */
            readonly poster_asset_id: string | null;
            /** Rank */
            readonly rank: number;
            readonly seek: components["schemas"]["Seek"] | null;
            /**
             * Segment
             * @enum {string}
             */
            readonly segment: "daytime" | "overnight";
            /** Snippet */
            readonly snippet: string;
            readonly source_interval: components["schemas"]["weather__api__models__Interval"];
            /** Video Id */
            readonly video_id: string;
            readonly video_interval: components["schemas"]["weather__api__models__Interval"];
        };
        /**
         * SearchRequest
         * @description Explicit UTC bounds; empty or whitespace-only text browses newest first (numeric).
         */
        readonly SearchRequest: {
            /**
             * Cameras
             * @default []
             */
            readonly cameras: readonly string[];
            /**
             * End
             * Format: date-time
             */
            readonly end: string;
            /**
             * Predicates
             * @default []
             */
            readonly predicates: readonly components["schemas"]["Predicate"][];
            /**
             * Query
             * @default
             */
            readonly query: string;
            /**
             * Segments
             * @default []
             */
            readonly segments: readonly ("daytime" | "overnight")[];
            /**
             * Start
             * Format: date-time
             */
            readonly start: string;
            /**
             * Top K
             * @default 10
             */
            readonly top_k: number;
        };
        /** SearchResults */
        readonly SearchResults: {
            /** Count */
            readonly count: number;
            /** Degraded Reason */
            readonly degraded_reason: ("embedding_unavailable" | "embedding_busy" | "lexical_no_terms") | null;
            /**
             * Generated At
             * Format: date-time
             */
            readonly generated_at: string;
            /** Hits */
            readonly hits: readonly components["schemas"]["SearchHit"][];
            /**
             * Mode
             * @enum {string}
             */
            readonly mode: "hybrid_exact" | "lexical" | "numeric";
            /** Profile Id */
            readonly profile_id: string | null;
        };
        /**
         * Seek
         * @description A playback offset inside the hit's cited media interval and its source instant.
         */
        readonly Seek: {
            /** Media Seconds */
            readonly media_seconds: number;
            /**
             * Source At
             * Format: date-time
             */
            readonly source_at: string;
        };
        /** Timelapse */
        readonly Timelapse: {
            /** Allocated Frames */
            readonly allocated_frames: number;
            /** Camera */
            readonly camera: string;
            /**
             * Cycle Date
             * Format: date
             */
            readonly cycle_date: string;
            readonly keogram: components["schemas"]["Asset"] | null;
            /**
             * Keogram State
             * @enum {string}
             */
            readonly keogram_state: "pending" | "published" | "partial" | "unavailable" | "withdrawn";
            /** Native Download Url */
            readonly native_download_url: string | null;
            readonly poster: components["schemas"]["Asset"] | null;
            /** Reason */
            readonly reason: string | null;
            /** Renditions */
            readonly renditions: readonly components["schemas"]["Rendition"][];
            /** Revision */
            readonly revision: number;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            readonly schema_version: 1;
            /**
             * Segment
             * @enum {string}
             */
            readonly segment: "daytime" | "overnight";
            readonly sidecar: components["schemas"]["Asset"] | null;
            readonly source_interval: components["schemas"]["weather__api__timelapses__Interval"];
            /**
             * State
             * @enum {string}
             */
            readonly state: "published" | "partial" | "unavailable" | "withdrawn";
            /**
             * Updated At
             * Format: date-time
             */
            readonly updated_at: string;
            /**
             * Video Id
             * Format: uuid
             */
            readonly video_id: string;
            /** Withdrawn */
            readonly withdrawn: boolean;
        };
        /** TimelapsePage */
        readonly TimelapsePage: {
            /**
             * Generated At
             * Format: date-time
             */
            readonly generated_at: string;
            /** Items */
            readonly items: readonly components["schemas"]["Timelapse"][];
            /** Next Cursor */
            readonly next_cursor: string | null;
        };
        /**
         * Interval
         * @description Half-open UTC interval [start, end).
         */
        readonly weather__api__models__Interval: {
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
        /** Interval */
        readonly weather__api__timelapses__Interval: {
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
    readonly getEvents: {
        readonly parameters: {
            readonly query?: {
                readonly camera?: ("skycam_east" | "skycam_west" | "frontyard_1") | null;
                readonly cursor?: string | null;
                readonly day?: string | null;
                readonly limit?: number;
                readonly order?: "asc" | "desc";
                readonly origin?: ("measured" | "visual" | "synthesis" | "manual") | null;
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
                    readonly "application/json": components["schemas"]["Events"];
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
    readonly getEvent: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly event_id: string;
            };
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
                    readonly "application/json": components["schemas"]["Event"];
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
            /** @description No such public event. */
            readonly 404: {
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
    readonly search: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["SearchRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["SearchResults"];
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
            /** @description Search is busy; retry after Retry-After. */
            readonly 429: {
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
    readonly listTimelapses: {
        readonly parameters: {
            readonly query?: {
                readonly camera?: string | null;
                readonly cursor?: string | null;
                readonly cycle_date?: string | null;
                /** @description First cycle date, inclusive */
                readonly from?: string | null;
                readonly limit?: number;
                /** @description YYYY-MM */
                readonly month?: string | null;
                readonly segment?: string | null;
                readonly state?: string | null;
                /** @description Last cycle date, inclusive */
                readonly to?: string | null;
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
                    readonly "application/json": components["schemas"]["TimelapsePage"];
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
            /** @description The catalog is temporarily unavailable. */
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
    readonly getTimelapse: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly video_id: string;
            };
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
                    readonly "application/json": components["schemas"]["Timelapse"];
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
            /** @description No such timelapse. */
            readonly 404: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description The catalog is temporarily unavailable. */
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
