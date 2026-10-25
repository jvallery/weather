// SPDX-License-Identifier: MIT
/**
 * The private editor (#44), behind Authentik forward-auth at the edge. It has no loader: the
 * server renders a static shell, and every private read and write is a same-origin browser fetch
 * to /api/v1/operator/*, so private data is never serialized into SSR or hydration. Drafts live
 * only in React state; a mutation is sent once and never retried automatically.
 */
import { useEffect, useMemo, useState } from "react";
import { parseEvents, parseTimelapses } from "../lib/weather-api";
import {
  buildPatch, draftFrom, MAX_DESCRIPTION, MAX_NOTE, manualEventProblems, operatorApi, UUID_PATTERN,
  type Draft, type ManualEvent, type OperatorProblem, type Result, type Subject, type Tag,
} from "../lib/operator-api";

export const meta = () => [{ title: "Editor · Weather" }, { name: "robots", content: "noindex, nofollow" }];
type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;
type Recent = { id: string; label: string };

export default function EditorRoute() {
  const [browser, setBrowser] = useState(false);
  useEffect(() => setBrowser(true), []);
  // The SSR shell and the first client render match; private data only arrives after mount.
  if (!browser) return <><h1>Private editor</h1><p>Loading the editor…</p></>;
  return <Editor fetcher={(input, init) => fetch(input, init)} />;
}

function problemText(problem: OperatorProblem, status: number): string {
  if (status === 401) return "Not signed in. Reload the page to sign in through Authentik.";
  if (status === 403 && problem.code === "origin_rejected") return "The request was refused as cross-origin.";
  if (status === 403) return "This account is not on the editor allowlist.";
  if (status === 404) return "No such video or event.";
  return problem.message;
}

export function Editor({ fetcher }: { fetcher: Fetcher }) {
  const api = useMemo(() => operatorApi(fetcher), [fetcher]);
  const [operator, setOperator] = useState<string | null>(null);
  const [denied, setDenied] = useState<string | null>(null);
  const [recent, setRecent] = useState<{ videos: Recent[]; events: Recent[] }>({ videos: [], events: [] });
  const [wanted, setWanted] = useState("");
  const [subject, setSubject] = useState<Subject | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [message, setMessage] = useState<{ kind: "fault" | "notice"; text: string } | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [rebaseRequired, setRebaseRequired] = useState(false);

  useEffect(() => {
    let live = true;
    void api.session().then((result) => {
      if (!live) return;
      if (result.ok) setOperator(result.data.username); else setDenied(problemText(result.problem, result.status));
    });
    // Public lists to pick from; fetched in the browser, read-only.
    void Promise.all([
      fetcher("/api/v1/timelapses?limit=20", { credentials: "same-origin" }).then((r) => r.json()).then(parseTimelapses).catch(() => null),
      fetcher("/api/v1/events?limit=20&order=desc", { credentials: "same-origin" }).then((r) => r.json()).then(parseEvents).catch(() => null),
    ]).then(([videos, events]) => {
      if (!live) return;
      setRecent({
        videos: (videos?.items || []).map((v) => ({ id: v.video_id, label: `${v.camera} · ${v.cycle_date} · ${v.segment}` })),
        events: (events?.items || []).map((e) => ({ id: e.id, label: `${e.label} · ${e.source_interval.start}` })),
      });
    });
    return () => { live = false; };
  }, [api, fetcher]);

  async function load(id: string, keepDraft = false) {
    setMessage(null); setErrors([]); setConflict(false); setRebaseRequired(false);
    if (!UUID_PATTERN.test(id)) { setMessage({ kind: "fault", text: "Enter a video or event ID (a lowercase UUID)." }); return; }
    const result = await api.subject(id);
    if (!result.ok) { setMessage({ kind: "fault", text: problemText(result.problem, result.status) }); return; }
    setSubject(result.data);
    if (!keepDraft || !draft) setDraft(draftFrom(result.data));
  }

  function settle(result: Result<Subject>, saved: string) {
    if (result.ok) {
      setSubject(result.data); setDraft(draftFrom(result.data)); setConflict(false); setRebaseRequired(false);
      setMessage({ kind: "notice", text: `${saved} Editorial revision ${result.data.revision}.` });
      return;
    }
    // The draft is kept on every failure; nothing is resent automatically.
    if (result.status === 409 && result.problem.code === "revision_conflict") setConflict(true);
    if (result.status === 409 && result.problem.code === "rebase_required") setRebaseRequired(true);
    setMessage({ kind: "fault", text: problemText(result.problem, result.status) });
  }

  async function save(rebase: boolean) {
    if (!subject || !draft || saving) return;
    const { body, errors: found } = buildPatch(subject, draft, rebase);
    setErrors(found);
    if (!body) return;
    setSaving(true);
    try { settle(await api.patch(subject.subject_id, body), "Saved."); } finally { setSaving(false); }
  }

  async function create(event: ManualEvent) {
    setSaving(true);
    try {
      const result = await api.manualEvent(event);
      settle(result, "Manual event created.");
      if (result.ok) setWanted(result.data.subject_id);
    } finally { setSaving(false); }
  }

  if (denied) return <><h1>Private editor</h1><p className="fault" role="alert">{denied}</p></>;
  return <div className="editor">
    <h1>Private editor</h1>
    <p className="small">{operator ? `Signed in as ${operator}.` : "Checking your sign-in…"} Changes are audited, revisioned and applied to the public site at once. Favorites, private tags and notes stay private.</p>
    <section aria-labelledby="pick"><h2 id="pick">Choose a video or event</h2>
      <form className="filters" onSubmit={(e) => { e.preventDefault(); void load(wanted.trim()); }}>
        <label>Video or event ID<input name="subject" value={wanted} onChange={(e) => setWanted(e.target.value)} autoComplete="off" spellCheck={false} /></label>
        <button type="submit">Open</button>
      </form>
      <div className="grid two">
        <RecentList title="Recent timelapses" items={recent.videos} onPick={(id) => { setWanted(id); void load(id); }} />
        <RecentList title="Recent events" items={recent.events} onPick={(id) => { setWanted(id); void load(id); }} />
      </div>
    </section>
    {message && <p className={message.kind === "fault" ? "fault" : "notice"} role={message.kind === "fault" ? "alert" : "status"}>{message.text}</p>}
    {conflict && subject && <p className="notice">Someone saved this subject first. <button type="button" onClick={() => void load(subject.subject_id, true)}>Reload the subject and keep my draft</button></p>}
    {subject && draft && <SubjectForm subject={subject} draft={draft} setDraft={setDraft} errors={errors} saving={saving} rebaseRequired={rebaseRequired} onSave={save} />}
    <ManualEventForm saving={saving} onCreate={create} />
  </div>;
}

function RecentList({ title, items, onPick }: { title: string; items: Recent[]; onPick: (id: string) => void }) {
  return <div className="card"><h3>{title}</h3>{items.length === 0 ? <p className="small">None loaded.</p> : <ul className="editor-picks">{items.map((item) => <li key={item.id}><button type="button" onClick={() => onPick(item.id)}>{item.label}</button></li>)}</ul>}</div>;
}

function Hash({ value }: { value: string | null | undefined }) {
  return <span className="monospace text-break">{value ? value.slice(0, 16) : "none"}</span>;
}

function SubjectForm({ subject, draft, setDraft, errors, saving, rebaseRequired, onSave }: {
  subject: Subject; draft: Draft; setDraft: (draft: Draft) => void; errors: string[]; saving: boolean; rebaseRequired: boolean; onSave: (rebase: boolean) => Promise<void>;
}) {
  const set = (changes: Partial<Draft>) => setDraft({ ...draft, ...changes });
  const stale = Boolean(subject.description?.stale || subject.event?.stale);
  const [tagName, setTagName] = useState("");
  const [tagVisibility, setTagVisibility] = useState<Tag["visibility"]>("public");
  const heading = subject.video ? `${subject.video.camera} · ${subject.video.cycle_date} · ${subject.video.segment}` : `Event · ${subject.event?.kind || ""}`;
  return <section aria-labelledby="subject-heading" className="card editor-subject">
    <h2 id="subject-heading">{heading}</h2>
    <p className="small">Editorial revision {subject.revision}{subject.updated_at ? ` · updated ${subject.updated_at}` : ""} · <span className="monospace">{subject.subject_id}</span></p>
    {(stale || rebaseRequired) && <EvidenceCompare subject={subject} />}
    <form className="editor-form" onSubmit={(e) => { e.preventDefault(); void onSave(false); }}>
      {subject.description && <fieldset><legend>Description</legend>
        <p className="small">Public now: {subject.description.active ? "the human description" : subject.description.effective ? "the validated AI description" : "no description"}.</p>
        {subject.description.automated && <details><summary>Current AI description</summary><p>{subject.description.automated}</p></details>}
        <label>Human description ({draft.description.length.toLocaleString("en-US")} of {MAX_DESCRIPTION.toLocaleString("en-US")})<textarea name="description" rows={8} value={draft.description} disabled={draft.clearDescription} onChange={(e) => set({ description: e.target.value })} /></label>
        {subject.description.human !== null && <label className="inline"><input type="checkbox" checked={draft.clearDescription} onChange={(e) => set({ clearDescription: e.target.checked })} /> Clear the human description (falls back to the validated AI description, if any)</label>}
      </fieldset>}
      {subject.event && <fieldset><legend>Event correction</legend>
        <p className="small">{subject.event.origin} · {subject.event.basis} · {subject.event.start} to {subject.event.end}{subject.event.camera ? ` · ${subject.event.camera}` : " · station-wide"}</p>
        <label>Label<input name="label" value={draft.label} maxLength={240} onChange={(e) => set({ label: e.target.value })} /></label>
        <label>Visibility<select name="visibility" value={draft.visibility} onChange={(e) => set({ visibility: e.target.value as Draft["visibility"] })}><option value="public">Public</option><option value="private">Private</option><option value="withdrawn">Withdrawn</option></select></label>
      </fieldset>}
      <fieldset><legend>Tags</legend>
        {draft.tags.length === 0 ? <p className="small">No tags.</p> : <ul className="tag-list">{draft.tags.map((tag) => <li key={tag.tag}><span className="badge">{tag.tag} · {tag.visibility}</span> <button type="button" onClick={() => set({ tags: draft.tags.filter((item) => item.tag !== tag.tag) })}>Remove {tag.tag}</button></li>)}</ul>}
        <div className="filters"><label>New tag<input name="tag" value={tagName} onChange={(e) => setTagName(e.target.value)} autoComplete="off" /></label>
          <label>Visibility<select name="tag-visibility" value={tagVisibility} onChange={(e) => setTagVisibility(e.target.value as Tag["visibility"])}><option value="public">Public</option><option value="private">Private</option></select></label>
          <button type="button" onClick={() => { const name = tagName.trim(); if (name) { set({ tags: [...draft.tags.filter((item) => item.tag !== name), { tag: name, visibility: tagVisibility }] }); setTagName(""); } }}>Add tag</button></div>
      </fieldset>
      <fieldset><legend>Private</legend>
        <label className="inline"><input type="checkbox" name="favorite" checked={draft.favorite} onChange={(e) => set({ favorite: e.target.checked })} /> Favorite</label>
        <label>Private note ({draft.note.length.toLocaleString("en-US")} of {MAX_NOTE.toLocaleString("en-US")})<textarea name="note" rows={3} value={draft.note} disabled={draft.clearNote} onChange={(e) => set({ note: e.target.value })} /></label>
        {subject.note !== null && <label className="inline"><input type="checkbox" checked={draft.clearNote} onChange={(e) => set({ clearNote: e.target.checked })} /> Clear the note</label>}
      </fieldset>
      <label>Reason for this change (required, audited)<textarea name="reason" rows={2} value={draft.reason} onChange={(e) => set({ reason: e.target.value })} /></label>
      {errors.length > 0 && <ul className="fault" role="alert">{errors.map((error) => <li key={error}>{error}</li>)}</ul>}
      <div className="actions"><button type="submit" disabled={saving}>{saving ? "Saving…" : "Save"}</button>
        {(stale || rebaseRequired) && <button type="button" disabled={saving} onClick={() => void onSave(true)}>Rebase onto the current evidence and save</button>}</div>
    </form>
    <h3>Audit</h3>
    {subject.audit.length === 0 ? <p className="small">No edits yet.</p> : <div className="table-wrap"><table><thead><tr><th>Revision</th><th>Action</th><th>By</th><th>When</th><th>Reason</th></tr></thead><tbody>{subject.audit.map((entry) => <tr key={entry.new_revision}><td>{entry.old_revision} → {entry.new_revision}</td><td>{entry.action}</td><td>{entry.actor}</td><td><time dateTime={entry.recorded_at}>{entry.recorded_at}</time></td><td>{entry.reason}</td></tr>)}</tbody></table></div>}
  </section>;
}

function EvidenceCompare({ subject }: { subject: Subject }) {
  const description = subject.description;
  const event = subject.event;
  return <div className="notice evidence-compare">
    <p><strong>The media or evidence changed since the human revision.</strong> It is withdrawn from the public site and search until you rebase it explicitly; the rebase is audited.</p>
    <div className="table-wrap"><table><thead><tr><th></th><th>Written against</th><th>Current</th></tr></thead><tbody>
      {description && <><tr><th>Rendition</th><td><Hash value={description.bound?.media_sha256} /></td><td><Hash value={description.current.media_sha256} /></td></tr>
        <tr><th>Evidence</th><td><Hash value={description.bound?.evidence_hash} /></td><td><Hash value={description.current.evidence_hash} /></td></tr>
        <tr><th>Text</th><td>{description.human || "none"}</td><td>{description.automated || "no AI description"}</td></tr></>}
      {event && <><tr><th>Revision</th><td>{event.human ? event.human.revision : "none"}</td><td>{event.current.revision}</td></tr>
        <tr><th>Label</th><td>{event.human?.label || "none"}</td><td>{event.current.label || "none"}</td></tr>
        <tr><th>Visibility</th><td>{event.human?.visibility || "none"}</td><td>{event.current.visibility}{event.current.withdrawal_reason ? ` (${event.current.withdrawal_reason})` : ""}</td></tr>
        <tr><th>Evidence</th><td><Hash value={event.human?.evidence_hash} /></td><td><Hash value={event.current.evidence_hash} /></td></tr></>}
    </tbody></table></div>
  </div>;
}

function ManualEventForm({ saving, onCreate }: { saving: boolean; onCreate: (event: ManualEvent) => Promise<void> }) {
  const [value, setValue] = useState({ reason: "", label: "", basis: "visual" as ManualEvent["basis"], start: "", end: "", camera: "" });
  const [errors, setErrors] = useState<string[]>([]);
  const set = (changes: Partial<typeof value>) => setValue({ ...value, ...changes });
  return <section aria-labelledby="manual" className="card"><h2 id="manual">New manual event</h2>
    <form className="editor-form" onSubmit={(e) => {
      e.preventDefault();
      const found = manualEventProblems(value);
      setErrors(found);
      if (found.length || saving) return;
      void onCreate({ reason: value.reason, label: value.label, basis: value.basis, start: value.start, end: value.end, camera: value.camera ? value.camera as ManualEvent["camera"] : null });
    }}>
      <label>Label<input name="manual-label" value={value.label} maxLength={240} onChange={(e) => set({ label: e.target.value })} /></label>
      <label>Basis<select name="basis" value={value.basis} onChange={(e) => set({ basis: e.target.value as ManualEvent["basis"] })}><option value="visual">Visual</option><option value="instrument">Instrument</option><option value="inference">Inference</option></select></label>
      <div className="filters"><label>Start (UTC)<input name="start" value={value.start} placeholder="2026-10-24T18:00:00Z" onChange={(e) => set({ start: e.target.value })} /></label>
        <label>End (UTC)<input name="end" value={value.end} placeholder="2026-10-24T19:00:00Z" onChange={(e) => set({ end: e.target.value })} /></label></div>
      <label>Camera<select name="camera" value={value.camera} onChange={(e) => set({ camera: e.target.value })}><option value="">Station-wide</option><option value="skycam_east">East sky camera</option><option value="skycam_west">West sky camera</option><option value="frontyard_1">Front yard camera</option></select></label>
      <label>Reason (required, audited)<textarea name="manual-reason" rows={2} value={value.reason} onChange={(e) => set({ reason: e.target.value })} /></label>
      {errors.length > 0 && <ul className="fault" role="alert">{errors.map((error) => <li key={error}>{error}</li>)}</ul>}
      <div className="actions"><button type="submit" disabled={saving}>Create manual event</button></div>
    </form>
  </section>;
}
