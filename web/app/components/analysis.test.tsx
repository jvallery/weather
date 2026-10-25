// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { AnalysisPanel } from "./analysis";
import { analysisField, ANALYSIS_LABEL } from "../lib/weather-api";
import variants from "../fixtures/analysis.json";

const parse = (value: unknown) => analysisField.parse(value);

test("every state carries the AI label, its state and, when complete or degraded, provenance and coverage", () => {
  for (const [state, value] of Object.entries(variants)) {
    const html = renderToStaticMarkup(<AnalysisPanel analysis={parse(value)} />);
    expect(html).toContain(ANALYSIS_LABEL);
    expect(html).toContain(state === "pending" ? "Pending" : state === "failed" ? "Failed" : state === "degraded" ? "Degraded" : "Complete");
    if (state === "complete" || state === "degraded") {
      expect(html).toContain("Model llm.primary.longctx.mm");
      expect(html).toContain("prompt 1f2e3d4c5b6a7980");
      expect(html).toMatch(/Analysis coverage: 88% of the source interval reviewed · 11 of 13 hour windows/);
    } else {
      expect(html).toContain("No AI description is available.");
      expect(html).toContain("retried nightly");
    }
  }
  expect(renderToStaticMarkup(<AnalysisPanel analysis={parse(variants.complete)} />)).toContain("sampled frames and keogram");
  expect(renderToStaticMarkup(<AnalysisPanel analysis={parse(variants.degraded)} />)).toContain("sampled frames only");
});

test("model text is escaped, unknown fields are dropped and a broken analysis degrades alone", () => {
  const hostile = parse({ ...variants.complete, description: "<img src=x onerror=alert(1)>", claims: [{ text: "<script>x</script>", basis: "oracle" }] });
  const html = renderToStaticMarkup(<AnalysisPanel analysis={hostile} />);
  expect(html).not.toContain("<img");
  expect(html).not.toContain("<script>");
  expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
  expect(html).toContain("Inference");
  const degraded = parse(variants.degraded);
  expect("raw_response" in degraded).toBe(false);
  expect(renderToStaticMarkup(<AnalysisPanel analysis={degraded} />)).not.toContain("RAW-MODEL-TEXT-SENTINEL");
  expect(parse({ state: 42 }).state).toBe("unavailable");
  expect(parse({ ...variants.complete, state: "oracle" }).state).toBe("unavailable");
});

test("the compact day-card form shows only the label, state and description", () => {
  const html = renderToStaticMarkup(<AnalysisPanel analysis={parse(variants.complete)} compact />);
  expect(html).toContain(ANALYSIS_LABEL);
  expect(html).not.toContain("Model llm.primary.longctx.mm");
});
