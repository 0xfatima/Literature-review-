import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import { describeError, jobState, newlyFinished, splitHighlight } from "@/lib/workspace";
import type { Job } from "@/lib/types";

const job = (id: string, status: string): Job => ({
  id,
  project_id: "p",
  type: "search.run",
  status,
  progress: 0,
  progress_message: null,
  attempts: 1,
  cancel_requested: false,
  result: null,
  error_summary: null,
  created_at: "",
  started_at: null,
  finished_at: null,
});

describe("workspace helpers", () => {
  it("distinguishes the five job states", () => {
    expect(
      ["queued", "running", "succeeded", "failed", "cancelled"].map((s) => jobState(job("x", s))),
    ).toEqual(["queued", "running", "completed", "failed", "cancelled"]);
  });

  it("detects jobs that finished since the last poll", () => {
    const before = [job("a", "running"), job("b", "queued"), job("c", "succeeded")];
    const after = [job("a", "succeeded"), job("b", "running"), job("c", "succeeded")];
    expect(newlyFinished(before, after).map((j) => j.id)).toEqual(["a"]);
  });

  it("gives understandable error messages", () => {
    expect(describeError(new ApiError(401, "x", "m", null, null), "f")).toMatch(/Sign in/);
    expect(describeError(new ApiError(500, "x", "m", null, null), "f")).toMatch(/server had a problem/);
    expect(describeError(new ApiError(409, "x", "Already running.", null, null), "f")).toBe(
      "Already running.",
    );
    expect(describeError(new TypeError("fetch failed"), "f")).toMatch(/Network error/);
    expect(describeError("weird", "Fallback")).toBe("Fallback");
  });

  it("highlights case-insensitively", () => {
    expect(splitHighlight("We Enrolled 120 adults", "enrolled 120")).toEqual([
      "We ",
      "Enrolled 120",
      " adults",
    ]);
    expect(splitHighlight("text", "none")).toBeNull();
  });
});
