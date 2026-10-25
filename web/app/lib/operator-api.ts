// SPDX-License-Identifier: MIT
/**
 * The private editor's client (#44). Types come only from the generated operator OpenAPI types;
 * parsing is lenient like weather-api.ts. Every call is a same-origin browser fetch: nothing here
 * runs during server rendering, nothing is persisted (no localStorage, sessionStorage, IndexedDB
 * or service worker) and a mutation is sent exactly once, never retried automatically.
 */
import { z } from "zod";
import type { components } from "../generated/operator-api";
z.config({ jitless: true });

type Schemas = components["schemas"];
export type Subject = Schemas["Subject"];
export type Tag = Schemas["Tag"];
export type Operator = Schemas["Operator"];
export type OperatorProblem = Schemas["OperatorProblem"];
export type ManualEvent = Schemas["ManualEvent"];
export type EventChange = { label?: string; visibility?: "public" | "private" | "withdrawn" };
/** The PATCH changes object: an omitted field is unchanged; null clears description or note. */
export type Changes = { description?: string | null; tags?: Tag[]; favorite?: boolean; note?: string | null; rebase?: true; event?: EventChange };
export type SubjectPatch = { expected_revision: number; reason: string; changes: Changes };

export const MAX_DESCRIPTION = 8000;
export const MAX_NOTE = 4000;
export const MAX_REASON = 2000;
export const MAX_LABEL = 240;
export const MAX_TAGS = 32;
export const TAG_PATTERN = /^[a-z0-9][a-z0-9 _-]{0,47}$/;
export const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export const UTC_PATTERN = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$/;
// Mirrors the server: tabs and newlines are allowed, other control characters never are.
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u2028\u2029]/;

const text = z.string();
const visibility = z.enum(["public", "private", "withdrawn"]).catch("private");
const evidence = z.object({ media_sha256: text.nullable().default(null), evidence_hash: text.nullable().default(null) });
const tag = z.object({ tag: text, visibility: z.enum(["public", "private"]).catch("private") });
const eventRevision = z.object({
  revision: z.number(), label: text.nullable().default(null), visibility, withdrawal_reason: text.nullable().default(null),
  author_kind: text, evidence_hash: text,
});
const subject = z.object({
  subject_id: text, kind: z.enum(["video", "event"]), revision: z.number(), updated_at: text.nullable().default(null),
  video: z.object({
    camera: text, cycle_date: text, segment: z.enum(["daytime", "overnight"]), start: text, end: text, state: text, withdrawn: z.boolean(),
  }).nullable().default(null),
  description: z.object({
    human: text.nullable(), active: z.boolean(), stale: z.boolean(), effective: text.nullable(), automated: text.nullable(),
    bound: evidence.nullable().default(null), current: evidence,
  }).nullable().default(null),
  event: z.object({
    kind: text, origin: text, basis: text, camera: text.nullable().default(null), start: text, end: text,
    current: eventRevision, human: eventRevision.nullable().default(null), stale: z.boolean(),
  }).nullable().default(null),
  tags: z.array(tag).default([]), favorite: z.boolean(), note: text.nullable().default(null),
  audit: z.array(z.object({
    action: z.enum(["edit", "rebase", "manual_event"]).catch("edit"), actor: text, recorded_at: text, reason: text,
    old_revision: z.number(), new_revision: z.number(),
  })).default([]),
});
const operator = z.object({ username: text });
const problem = z.object({ code: text, message: text, current_revision: z.number().nullable().default(null) });
// The parsed shapes must stay assignable to the generated types; tsc fails on drift.
const checks: [(value: z.infer<typeof subject>) => Subject, (value: z.infer<typeof operator>) => Operator, (value: z.infer<typeof problem>) => OperatorProblem] = [(value) => value, (value) => value, (value) => value];
void checks;

export type Result<T> = { ok: true; status: number; data: T } | { ok: false; status: number; problem: OperatorProblem };
type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;
const FAILED: OperatorProblem = { code: "unavailable", message: "The editor is temporarily unavailable.", current_revision: null };

async function call<T>(fetcher: Fetcher, path: string, schema: z.ZodType<T>, init: RequestInit = {}): Promise<Result<T>> {
  let response: Response;
  try {
    response = await fetcher(path, { ...init, credentials: "same-origin", cache: "no-store", redirect: "manual", headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) } });
  } catch {
    return { ok: false, status: 0, problem: FAILED };
  }
  // An expired Authentik session answers the fetch with a redirect to sign in.
  if (response.type === "opaqueredirect" || (response.status >= 300 && response.status < 400)) return { ok: false, status: 401, problem: { code: "unauthenticated", message: "Sign in again.", current_revision: null } };
  let body: unknown = null;
  try { body = await response.json(); } catch { body = null; }
  if (response.ok) {
    const parsed = schema.safeParse(body);
    return parsed.success ? { ok: true, status: response.status, data: parsed.data } : { ok: false, status: response.status, problem: FAILED };
  }
  const parsed = problem.safeParse(body);
  return { ok: false, status: response.status, problem: parsed.success ? parsed.data : { ...FAILED, code: `http_${response.status}` } };
}

export const operatorApi = (fetcher: Fetcher) => ({
  session: () => call(fetcher, "/api/v1/operator/session", operator),
  subject: (id: string) => call(fetcher, `/api/v1/operator/subjects/${encodeURIComponent(id)}`, subject),
  patch: (id: string, body: SubjectPatch) => call(fetcher, `/api/v1/operator/subjects/${encodeURIComponent(id)}`, subject, { method: "PATCH", body: JSON.stringify(body) }),
  manualEvent: (body: ManualEvent) => call(fetcher, "/api/v1/operator/events", subject, { method: "POST", body: JSON.stringify(body) }),
});

/** The editable draft lives only in React state. */
export type Draft = {
  reason: string; description: string; clearDescription: boolean; tags: Tag[]; favorite: boolean;
  note: string; clearNote: boolean; label: string; visibility: "public" | "private" | "withdrawn";
};
export function draftFrom(subject: Subject): Draft {
  return {
    reason: "", description: subject.description?.human ?? "", clearDescription: false, tags: [...subject.tags], favorite: subject.favorite,
    note: subject.note ?? "", clearNote: false, label: subject.event?.current.label ?? "", visibility: subject.event?.current.visibility ?? "public",
  };
}

function plainProblem(value: string, name: string, max: number): string | null {
  if (!value.trim()) return `${name} cannot be blank.`;
  if (value.length > max) return `${name} is ${value.length.toLocaleString("en-US")} characters; the limit is ${max.toLocaleString("en-US")}. Nothing is truncated.`;
  if (CONTROL.test(value)) return `${name} contains control characters.`;
  return null;
}
const tagKey = (tags: readonly Tag[]) => JSON.stringify([...tags].map((item) => [item.visibility, item.tag]).sort());

/** Only changed fields are sent; client checks mirror the server and never truncate. */
export function buildPatch(subject: Subject, draft: Draft, rebase = false): { body: SubjectPatch | null; errors: string[] } {
  const errors: string[] = [];
  const changes: Changes = {};
  const reason = plainProblem(draft.reason, "Reason", MAX_REASON);
  if (reason) errors.push(reason);
  if (subject.kind === "video") {
    const human = subject.description?.human ?? null;
    if (draft.clearDescription) { if (human !== null) changes.description = null; }
    else if (draft.description !== (human ?? "")) {
      const issue = plainProblem(draft.description, "Description", MAX_DESCRIPTION);
      if (issue) errors.push(issue); else changes.description = draft.description;
    }
  } else if (subject.event) {
    const event: EventChange = {};
    if (draft.label !== (subject.event.current.label ?? "")) {
      const issue = plainProblem(draft.label, "Label", MAX_LABEL) || (/[\n\t]/.test(draft.label) ? "Label must be one line." : null);
      if (issue) errors.push(issue); else event.label = draft.label;
    }
    if (draft.visibility !== subject.event.current.visibility) event.visibility = draft.visibility;
    if (Object.keys(event).length) changes.event = event;
  }
  if (tagKey(draft.tags) !== tagKey(subject.tags)) {
    const names = draft.tags.map((item) => item.tag);
    if (draft.tags.length > MAX_TAGS) errors.push(`At most ${MAX_TAGS} tags.`);
    else if (new Set(names).size !== names.length) errors.push("Each tag may appear once.");
    else if (names.some((name) => !TAG_PATTERN.test(name))) errors.push("Tags are lowercase letters, digits, spaces, _ or -, up to 48 characters.");
    else changes.tags = draft.tags.map((item) => ({ tag: item.tag, visibility: item.visibility }));
  }
  if (draft.favorite !== subject.favorite) changes.favorite = draft.favorite;
  if (draft.clearNote) { if (subject.note !== null) changes.note = null; }
  else if (draft.note !== (subject.note ?? "")) {
    const issue = plainProblem(draft.note, "Note", MAX_NOTE);
    if (issue) errors.push(issue); else changes.note = draft.note;
  }
  if (rebase) changes.rebase = true;
  if (!errors.length && !Object.keys(changes).length) errors.push("Nothing changed.");
  return errors.length ? { body: null, errors } : { body: { expected_revision: subject.revision, reason: draft.reason, changes }, errors };
}

export function manualEventProblems(value: { reason: string; label: string; start: string; end: string }): string[] {
  const errors = [plainProblem(value.reason, "Reason", MAX_REASON), plainProblem(value.label, "Label", MAX_LABEL)].filter((item): item is string => item !== null);
  if (!UTC_PATTERN.test(value.start) || !UTC_PATTERN.test(value.end) || Number.isNaN(Date.parse(value.start)) || Number.isNaN(Date.parse(value.end))) errors.push("Start and end are UTC instants such as 2026-10-24T18:00:00Z.");
  else if (Date.parse(value.start) >= Date.parse(value.end)) errors.push("Start must precede end.");
  return errors;
}
