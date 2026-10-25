// SPDX-License-Identifier: MIT
// Generated from code-first Pydantic OpenAPI; run scripts/ci.sh contracts --write.
export interface paths {
    readonly "/api/v1/operator/events": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /** Manual Event */
        readonly post: operations["createManualEvent"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/operator/session": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Session */
        readonly get: operations["getOperator"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/operator/subjects/{subject_id}": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Get Subject */
        readonly get: operations["getSubject"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        /** Patch Subject */
        readonly patch: operations["patchSubject"];
        readonly trace?: never;
    };
    readonly "/api/v1/operator/telemetry/receipts": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Get Receipts */
        readonly get: operations["getReceipts"];
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
        /** AuditEntry */
        readonly AuditEntry: {
            /**
             * Action
             * @enum {string}
             */
            readonly action: "edit" | "rebase" | "manual_event";
            /** Actor */
            readonly actor: string;
            /** New Revision */
            readonly new_revision: number;
            /** Old Revision */
            readonly old_revision: number;
            /** Reason */
            readonly reason: string;
            /**
             * Recorded At
             * Format: date-time
             */
            readonly recorded_at: string;
        };
        /** Changes */
        readonly Changes: {
            /** Description */
            readonly description?: string | null;
            readonly event?: components["schemas"]["EventChange"] | null;
            /** Favorite */
            readonly favorite?: boolean | null;
            /** Note */
            readonly note?: string | null;
            /** Rebase */
            readonly rebase?: true | null;
            /** Tags */
            readonly tags?: readonly components["schemas"]["Tag"][];
        };
        /**
         * DescriptionState
         * @description Old versus new evidence: what the human text was written against, and what is current.
         */
        readonly DescriptionState: {
            /** Active */
            readonly active: boolean;
            /** Automated */
            readonly automated: string | null;
            readonly bound: components["schemas"]["Evidence"] | null;
            readonly current: components["schemas"]["Evidence"];
            /** Effective */
            readonly effective: string | null;
            /** Human */
            readonly human: string | null;
            /** Stale */
            readonly stale: boolean;
        };
        /**
         * EventChange
         * @description An event correction through #41's override hook: label or visibility only.
         */
        readonly EventChange: {
            /** Label */
            readonly label?: string | null;
            /** Visibility */
            readonly visibility?: ("public" | "private" | "withdrawn") | null;
        };
        /** EventRevision */
        readonly EventRevision: {
            /** Author Kind */
            readonly author_kind: string;
            /** Evidence Hash */
            readonly evidence_hash: string;
            /** Label */
            readonly label: string | null;
            /** Revision */
            readonly revision: number;
            /**
             * Visibility
             * @enum {string}
             */
            readonly visibility: "public" | "private" | "withdrawn";
            /** Withdrawal Reason */
            readonly withdrawal_reason: string | null;
        };
        /** EventState */
        readonly EventState: {
            /** Basis */
            readonly basis: string;
            /** Camera */
            readonly camera: string | null;
            readonly current: components["schemas"]["EventRevision"];
            /**
             * End
             * Format: date-time
             */
            readonly end: string;
            readonly human: components["schemas"]["EventRevision"] | null;
            /** Kind */
            readonly kind: string;
            /** Origin */
            readonly origin: string;
            /** Stale */
            readonly stale: boolean;
            /**
             * Start
             * Format: date-time
             */
            readonly start: string;
        };
        /** Evidence */
        readonly Evidence: {
            /** Evidence Hash */
            readonly evidence_hash: string | null;
            /** Media Sha256 */
            readonly media_sha256: string | null;
        };
        /**
         * ManualEvent
         * @description A standalone manual annotation through #41's write_manual hook.
         */
        readonly ManualEvent: {
            /**
             * Basis
             * @enum {string}
             */
            readonly basis: "instrument" | "visual" | "inference";
            /** Camera */
            readonly camera?: ("skycam_east" | "skycam_west" | "frontyard_1") | null;
            /**
             * End
             * Format: date-time
             */
            readonly end: string;
            /** Evidence Ids */
            readonly evidence_ids?: readonly string[];
            /** Label */
            readonly label: string;
            /** Reason */
            readonly reason: string;
            /**
             * Start
             * Format: date-time
             */
            readonly start: string;
        };
        /** Operator */
        readonly Operator: {
            /** Username */
            readonly username: string;
        };
        /** OperatorProblem */
        readonly OperatorProblem: {
            /** Code */
            readonly code: string;
            /** Current Revision */
            readonly current_revision: number | null;
            /** Message */
            readonly message: string;
        };
        /** Receipt */
        readonly Receipt: {
            /** Bytes */
            readonly bytes: number;
            /** Bytes Base64 */
            readonly bytes_base64: string;
            /** Family */
            readonly family: string;
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Parse Reason */
            readonly parse_reason: string | null;
            /**
             * Parse State
             * @enum {string}
             */
            readonly parse_state: "parsed" | "unsupported" | "quarantined";
            /** Parser Revision */
            readonly parser_revision: string;
            /**
             * Received At
             * Format: date-time
             */
            readonly received_at: string;
            /** Sha256 */
            readonly sha256: string;
        };
        /** Receipts */
        readonly Receipts: {
            /**
             * Generated At
             * Format: date-time
             */
            readonly generated_at: string;
            /** Items */
            readonly items: readonly components["schemas"]["Receipt"][];
            /** Next Cursor */
            readonly next_cursor: string | null;
        };
        /** Subject */
        readonly Subject: {
            /** Audit */
            readonly audit: readonly components["schemas"]["AuditEntry"][];
            readonly description: components["schemas"]["DescriptionState"] | null;
            readonly event: components["schemas"]["EventState"] | null;
            /** Favorite */
            readonly favorite: boolean;
            /**
             * Kind
             * @enum {string}
             */
            readonly kind: "video" | "event";
            /** Note */
            readonly note: string | null;
            /** Revision */
            readonly revision: number;
            /**
             * Subject Id
             * Format: uuid
             */
            readonly subject_id: string;
            /** Tags */
            readonly tags: readonly components["schemas"]["Tag"][];
            /** Updated At */
            readonly updated_at: string | null;
            readonly video: components["schemas"]["VideoSummary"] | null;
        };
        /** SubjectPatch */
        readonly SubjectPatch: {
            readonly changes: components["schemas"]["Changes"];
            /** Expected Revision */
            readonly expected_revision: number;
            /** Reason */
            readonly reason: string;
        };
        /** Tag */
        readonly Tag: {
            /** Tag */
            readonly tag: string;
            /**
             * Visibility
             * @enum {string}
             */
            readonly visibility: "public" | "private";
        };
        /** VideoSummary */
        readonly VideoSummary: {
            /** Camera */
            readonly camera: string;
            /**
             * Cycle Date
             * Format: date
             */
            readonly cycle_date: string;
            /**
             * End
             * Format: date-time
             */
            readonly end: string;
            /**
             * Segment
             * @enum {string}
             */
            readonly segment: "daytime" | "overnight";
            /**
             * Start
             * Format: date-time
             */
            readonly start: string;
            /** State */
            readonly state: string;
            /** Withdrawn */
            readonly withdrawn: boolean;
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
    readonly createManualEvent: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["ManualEvent"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 201: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["Subject"];
                };
            };
            /** @description Invalid query. */
            readonly 400: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description No authenticated identity. */
            readonly 401: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description Not an allowlisted operator, or a cross-origin mutation. */
            readonly 403: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description No such subject. */
            readonly 404: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description Revision conflict or rebase required. */
            readonly 409: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description The body must be JSON. */
            readonly 415: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description Invalid change. */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description The editor is temporarily unavailable. */
            readonly 503: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
        };
    };
    readonly getOperator: {
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
                    readonly "application/json": components["schemas"]["Operator"];
                };
            };
            /** @description Invalid query. */
            readonly 400: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description No authenticated identity. */
            readonly 401: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description Not an allowlisted operator, or a cross-origin mutation. */
            readonly 403: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description No such subject. */
            readonly 404: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description The editor is temporarily unavailable. */
            readonly 503: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
        };
    };
    readonly getSubject: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly subject_id: string;
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
                    readonly "application/json": components["schemas"]["Subject"];
                };
            };
            /** @description Invalid query. */
            readonly 400: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description No authenticated identity. */
            readonly 401: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description Not an allowlisted operator, or a cross-origin mutation. */
            readonly 403: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description No such subject. */
            readonly 404: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description The editor is temporarily unavailable. */
            readonly 503: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
        };
    };
    readonly patchSubject: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly subject_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["SubjectPatch"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["Subject"];
                };
            };
            /** @description Invalid query. */
            readonly 400: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description No authenticated identity. */
            readonly 401: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description Not an allowlisted operator, or a cross-origin mutation. */
            readonly 403: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description No such subject. */
            readonly 404: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description Revision conflict or rebase required. */
            readonly 409: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description The body must be JSON. */
            readonly 415: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description Invalid change. */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description The editor is temporarily unavailable. */
            readonly 503: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
        };
    };
    readonly getReceipts: {
        readonly parameters: {
            readonly query: {
                readonly cursor?: string | null;
                readonly end: string;
                readonly family?: ("obs_st" | "rapid_wind" | "evt_precip" | "evt_strike" | "device_status" | "hub_status" | "obs_air" | "obs_sky" | "unknown") | null;
                readonly limit?: number;
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
                    readonly "application/json": components["schemas"]["Receipts"];
                };
            };
            /** @description Invalid query. */
            readonly 400: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description No authenticated identity. */
            readonly 401: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description Not an allowlisted operator, or a cross-origin mutation. */
            readonly 403: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description No such subject. */
            readonly 404: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
            /** @description The editor is temporarily unavailable. */
            readonly 503: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/problem+json": components["schemas"]["OperatorProblem"];
                };
            };
        };
    };
}
