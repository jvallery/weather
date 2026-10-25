// SPDX-License-Identifier: MIT
import { ANALYSIS_LABEL, EDITOR_LABEL, STATION_LABEL, type Analysis } from "../lib/weather-api";

/** The labelled description (#23): server-rendered template text (C18), as escaped text nodes. A
 * station-only summary and an editor's revision (#44) carry their own labels and no model
 * provenance. */
const STATES: Record<Analysis["state"], string> = {
  pending: "Pending · not analysed yet",
  complete: "Complete",
  degraded: "Degraded · not every hour was reviewed visually",
  failed: "Failed · no description could be validated",
  unavailable: "Unavailable · no playable media to analyse",
};
const REASONS: Record<string, string> = {
  cortex_unavailable: "The inference service was unavailable; it is retried nightly for 7 days.",
  cortex_busy: "The inference service was busy; it is retried nightly for 7 days.",
  cortex_auth: "The inference service refused the request; it is retried nightly for 7 days.",
  expired: "Retries ended after 7 days.",
  visual_partial: "Some sampled hours did not pass visual validation.",
  visual_invalid: "No sampled hour passed visual validation.",
  call_budget: "The analysis call budget ran out; the summary covers the hours reviewed.",
  slot_busy: "The inference service was busy; it is retried nightly for 7 days.",
  slot_unavailable: "The inference service was unavailable; it is retried nightly for 7 days.",
  no_evidence: "Neither validated imagery nor station measurements were available.",
  media_unavailable: "The published rendition could not be read.",
};
const BASIS = { visual: "Visual", instrument: "Measured" } as const;
const LABELS = { ai: ANALYSIS_LABEL, station: STATION_LABEL, editor: EDITOR_LABEL } as const;

function reason(analysis: Analysis): string | null {
  if (!analysis.reason) return null;
  return REASONS[analysis.reason] || (analysis.reason.startsWith("media_") ? REASONS.media_unavailable : null);
}

export function AnalysisPanel({ analysis, compact = false }: { analysis: Analysis; compact?: boolean }) {
  const coverage = analysis.coverage;
  const provenance = analysis.provenance;
  const why = reason(analysis);
  const ai = analysis.author === "ai";
  return <div className="analysis">
    <p className="provenance"><strong>{LABELS[analysis.author]}</strong> · {STATES[analysis.state]}</p>
    {analysis.description ? <p>{analysis.description}</p> : <p>No AI description is available.</p>}
    {analysis.stale && <p className="notice">Station measurements changed after this description; a refreshed summary is queued.</p>}
    {!compact && why && <p className="small">{why}</p>}
    {!compact && analysis.claims.length > 0 && <ul className="claims">{analysis.claims.map((claim, index) => <li key={index}><span className="status">{BASIS[claim.basis]}</span> {claim.text}</li>)}</ul>}
    {!compact && coverage && <p className="small">Analysis coverage: {(coverage.fraction * 100).toFixed(0)}% of the source interval reviewed · {coverage.windows_reviewed} of {coverage.windows_total} hour windows · sampled frames{provenance?.keogram_used ? " and keogram" : " only"}</p>}
    {!compact && provenance && ai && <p className="small">Model {provenance.model_route} ({provenance.model}) · prompt {provenance.prompt_revision} · schema {provenance.schema_revision} · {provenance.evidence_count.toLocaleString("en-US")} station records · {provenance.frames_sampled} frames · {provenance.refresh === "measurements" ? "summary refreshed after measurements changed" : "full analysis"}</p>}
    {!compact && analysis.limitations.length > 0 && <ul className="small">{analysis.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>}
  </div>;
}
