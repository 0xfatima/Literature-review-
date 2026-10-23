import { describe, expect, it } from "vitest";
import { impactText, isBusy, mergeCriteria, parseList, parseYear, statusText, undecided } from "./protocol";
import type { Criterion, WorkflowRun } from "./types";

const run = (over: Partial<WorkflowRun>): WorkflowRun => ({
  id: "r",
  project_id: "p",
  conversation_id: null,
  stage: "DRAFT",
  status: "awaiting_input",
  pending_question_id: null,
  pending_question: null,
  protocol_version_id: null,
  iteration_count: 0,
  max_iterations: 60,
  stop_reason: null,
  error_summary: null,
  stage_before_revision: null,
  row_version: 1,
  ...over,
});

describe("protocol helpers", () => {
  it("parses comma lists without blanks or case duplicates", () => {
    expect(parseList(" English, english ,French;; German\n")).toEqual(["English", "French", "German"]);
    expect(parseList("")).toEqual([]);
  });
  it("parses years strictly", () => {
    expect(parseYear("")).toBeNull();
    expect(parseYear(" 2010 ")).toBe(2010);
    expect(parseYear("20.5")).toBeNaN();
  });
  it("merges suggestions without duplicating existing criteria", () => {
    const cur: Criterion[] = [{ stable_key: "a", kind: "inclusion", text: "Adults" }];
    const sug: Criterion[] = [
      { stable_key: "a", kind: "inclusion", text: "dup by key" },
      { stable_key: "b", kind: "inclusion", text: " adults " },
      { stable_key: "c", kind: "exclusion", text: "Animals", origin: "suggested", state: "proposed" },
    ];
    expect(mergeCriteria(cur, sug).map((c) => c.stable_key)).toEqual(["a", "c"]);
    expect(undecided(mergeCriteria(cur, sug))).toBe(1);
  });
  it("busy only while the worker owns the step", () => {
    expect(isBusy(run({ status: "queued" }))).toBe(true);
    expect(isBusy(run({ status: "awaiting_input" }))).toBe(false);
    expect(isBusy(null)).toBe(false);
  });
  it("is honest about what comes after approval", () => {
    expect(statusText(run({ status: "idle", stage: "SEARCH" }))).toMatch(/not available yet/);
    expect(statusText(run({ status: "failed", error_summary: "Stopped" }))).toBe("Stopped");
  });
  it("describes impact without claiming results exist", () => {
    expect(impactText([], {})).toMatch(/No material change/);
    expect(impactText(["RECORD_SCREENING"], { RECORD_SCREENING: "Record screening" })).toMatch(
      /Record screening.*No search, screening or extraction results exist yet/,
    );
  });
});

import { elementRows } from "./protocol";
describe("elementRows", () => {
  it("lists a framework's elements in order with saved values", () => {
    const rows = elementRows("pico", [{ key: "outcome", label: "Outcome", value: "mood" }]);
    expect(rows.map((r) => r.key)).toEqual(["population", "intervention", "comparison", "outcome"]);
    expect(rows[3]?.value).toBe("mood");
  });
  it("has nothing to fill for no framework and one blank row for custom", () => {
    expect(elementRows("none", [])).toEqual([]);
    expect(elementRows(null, [])).toEqual([]);
    expect(elementRows("custom", [])).toHaveLength(1);
  });
});
