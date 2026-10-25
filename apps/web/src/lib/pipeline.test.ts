import { describe, expect, it } from "vitest";
import { isLive, stageStates } from "@/lib/pipeline";
import type { PipelineRunView } from "@/lib/types";

const run = (over: Partial<PipelineRunView>): PipelineRunView => ({
  id: "r",
  status: "waiting",
  stage: "screening",
  start_stage: "protocol",
  autonomy: {},
  pending: null,
  outputs: { protocol: {}, search: { papers: 4 } },
  counters: { skipped: [] },
  supersedes_id: null,
  rerun_reason: null,
  error_summary: null,
  created_at: "",
  finished_at: null,
  changes_since_previous_run: {},
  stages: ["protocol", "search", "screening", "extraction"].map((key) => ({ key, label: key })),
  events: [],
  ...over,
});

describe("pipeline helpers", () => {
  it("derives stage states from outputs and position", () => {
    expect(stageStates(run({}))).toEqual({
      protocol: "done",
      search: "done",
      screening: "current",
      extraction: "todo",
    });
    expect(stageStates(run({ counters: { skipped: ["screening"] }, stage: "extraction" })).screening).toBe(
      "skipped",
    );
    expect(stageStates(run({ start_stage: "screening", outputs: {} })).protocol).toBe("done");
  });

  it("knows which runs are live", () => {
    expect(isLive(run({}))).toBe(true);
    expect(isLive(run({ status: "paused" }))).toBe(false);
    expect(isLive(null)).toBe(false);
  });
});
