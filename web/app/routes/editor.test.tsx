// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, expect, test, vi } from "vitest";
import EditorRoute, { Editor } from "./editor";
import { buildPatch, draftFrom, type Subject } from "../lib/operator-api";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const VIDEO = "11111111-2222-4333-8444-555555555555";
const subject: Subject = {
  subject_id: VIDEO, kind: "video", revision: 3, updated_at: "2026-10-24T18:00:00Z",
  video: { camera: "skycam_east", cycle_date: "2026-10-24", segment: "daytime", start: "2026-10-24T13:00:00Z", end: "2026-10-25T00:00:00Z", state: "published", withdrawn: false },
  description: { human: "PRIVATE-HUMAN-DRAFT clouds", active: true, stale: false, effective: "PRIVATE-HUMAN-DRAFT clouds", automated: "AI text", bound: { media_sha256: "a".repeat(64), evidence_hash: "b".repeat(64) }, current: { media_sha256: "a".repeat(64), evidence_hash: "b".repeat(64) } },
  event: null, tags: [{ tag: "snow", visibility: "public" }, { tag: "PRIVATE-TAG".toLowerCase(), visibility: "private" }],
  favorite: false, note: "PRIVATE-NOTE", audit: [{ action: "edit", actor: "jason", recorded_at: "2026-10-24T18:00:00Z", reason: "fix", old_revision: 2, new_revision: 3 }],
};
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": status < 300 ? "application/json" : "application/problem+json" } });

function server(handler?: (url: string, init?: RequestInit) => Response | undefined) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const custom = handler?.(url, init);
    if (custom) return custom;
    if (url === "/api/v1/operator/session") return json(200, { username: "jason" });
    if (url.startsWith("/api/v1/timelapses")) return json(200, { generated_at: "2026-10-24T18:00:00Z", items: [], next_cursor: null });
    if (url.startsWith("/api/v1/events")) return json(200, { generated_at: "2026-10-24T18:00:00Z", items: [], next_cursor: null });
    if (url === `/api/v1/operator/subjects/${VIDEO}` && (!init?.method || init.method === "GET")) return json(200, subject);
    return json(500, { code: "unexpected", message: "unexpected" });
  });
}

async function open(fetcher: ReturnType<typeof server>) {
  await act(async () => { render(<Editor fetcher={fetcher} />); });
  fireEvent.change(screen.getByLabelText("Video or event ID"), { target: { value: VIDEO } });
  await act(async () => { fireEvent.click(screen.getByText("Open")); });
  await screen.findByText(/Editorial revision 3/);
}
const patches = (fetcher: ReturnType<typeof server>) => fetcher.mock.calls.filter(([, init]) => init?.method === "PATCH");

test("the server render is a static shell: no fetch, no private data", () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const html = renderToString(<EditorRoute />);
  expect(html).toContain("Loading the editor");
  expect(html).not.toMatch(/PRIVATE|jason|operator\/subjects/);
  expect(fetcher).not.toHaveBeenCalled();
});

test("a PATCH carries expected_revision, the reason and only the changed fields", async () => {
  const fetcher = server((url, init) => init?.method === "PATCH" ? json(200, { ...subject, revision: 4, favorite: true }) : undefined);
  await open(fetcher);
  fireEvent.click(screen.getByLabelText("Favorite"));
  fireEvent.change(screen.getByLabelText(/Reason for this change/), { target: { value: "Marked as a favorite" } });
  await act(async () => { fireEvent.click(screen.getByText("Save")); });
  const [[url, init]] = patches(fetcher);
  expect(url).toBe(`/api/v1/operator/subjects/${VIDEO}`);
  expect(init?.credentials).toBe("same-origin");
  expect(JSON.parse(String(init?.body))).toEqual({ expected_revision: 3, reason: "Marked as a favorite", changes: { favorite: true } });
  expect(await screen.findByText(/Saved\. Editorial revision 4/)).toBeTruthy();
});

test("a 409 is shown once, never retried, and the draft is kept", async () => {
  const fetcher = server((url, init) => init?.method === "PATCH" ? json(409, { code: "revision_conflict", message: "The subject changed; reload it.", current_revision: 4 }) : undefined);
  await open(fetcher);
  fireEvent.change(screen.getByLabelText(/Human description/), { target: { value: "Fog lifting by noon." } });
  fireEvent.change(screen.getByLabelText(/Reason for this change/), { target: { value: "Correct the AI" } });
  await act(async () => { fireEvent.click(screen.getByText("Save")); });
  await screen.findByText("The subject changed; reload it.");
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(patches(fetcher)).toHaveLength(1);
  expect((screen.getByLabelText(/Human description/) as HTMLTextAreaElement).value).toBe("Fog lifting by noon.");
  expect(screen.getByText("Reload the subject and keep my draft")).toBeTruthy();
});

test("a stale human revision shows old and new evidence and an explicit rebase", async () => {
  const stale: Subject = { ...subject, description: { ...subject.description!, active: false, stale: true, effective: "AI text", current: { media_sha256: "c".repeat(64), evidence_hash: "d".repeat(64) } } };
  const fetcher = server((url, init) => {
    if (init?.method === "PATCH") return json(200, { ...stale, revision: 4 });
    if (url === `/api/v1/operator/subjects/${VIDEO}`) return json(200, stale);
    return undefined;
  });
  await open(fetcher);
  expect(screen.getByText(/media or evidence changed/)).toBeTruthy();
  expect(screen.getByText("a".repeat(16))).toBeTruthy();
  expect(screen.getByText("c".repeat(16))).toBeTruthy();
  fireEvent.change(screen.getByLabelText(/Reason for this change/), { target: { value: "Still accurate" } });
  await act(async () => { fireEvent.click(screen.getByText("Rebase onto the current evidence and save")); });
  expect(JSON.parse(String(patches(fetcher)[0][1]?.body)).changes).toEqual({ rebase: true });
});

test("blank or over-long text is blocked client-side and never truncated", () => {
  const draft = { ...draftFrom(subject), reason: "why" };
  for (const description of ["   ", "x".repeat(8001)]) {
    const { body, errors } = buildPatch(subject, { ...draft, description });
    expect(body).toBeNull();
    expect(errors.join(" ")).toMatch(/blank|limit is 8,000/);
  }
  const exact = "x".repeat(8000);
  expect(buildPatch(subject, { ...draft, description: exact }).body?.changes.description).toBe(exact);
  expect(buildPatch(subject, { ...draft, clearDescription: true }).body?.changes).toEqual({ description: null });
  expect(buildPatch(subject, { ...draft, clearNote: true }).body?.changes).toEqual({ note: null });
  expect(buildPatch(subject, { ...draft, reason: "" }).body).toBeNull();
  expect(buildPatch(subject, draft).errors).toEqual(["Nothing changed."]);
});

test("an event correction sends only the event fields", () => {
  const event: Subject = {
    ...subject, kind: "event", video: null, description: null, tags: [], note: null,
    event: { kind: "manual", origin: "manual", basis: "visual", camera: null, start: "2026-10-24T18:00:00Z", end: "2026-10-24T19:00:00Z", current: { revision: 2, label: "Hail", visibility: "public", withdrawal_reason: null, author_kind: "operator", evidence_hash: "e".repeat(64) }, human: null, stale: false },
  };
  const draft = { ...draftFrom(event), reason: "Duplicate", visibility: "withdrawn" as const };
  expect(buildPatch(event, draft).body).toEqual({ expected_revision: 3, reason: "Duplicate", changes: { event: { visibility: "withdrawn" } } });
});

test("401 and 403 are explained without retrying", async () => {
  const fetcher = server((url) => url === "/api/v1/operator/session" ? json(403, { code: "forbidden", message: "Not an allowlisted operator." }) : undefined);
  await act(async () => { render(<Editor fetcher={fetcher} />); });
  expect(await screen.findByText("This account is not on the editor allowlist.")).toBeTruthy();
  expect(fetcher.mock.calls.filter(([url]) => url === "/api/v1/operator/session")).toHaveLength(1);
});
