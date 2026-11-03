import { describe, expect, it } from "vitest";
import {
  REVIEW_STEPS,
  VIEWS,
  currentStepIndex,
  matchesQuery,
  nextAfterClose,
  stepOfView,
  stepStates,
} from "./steps";

describe("review steps", () => {
  it("puts every view in exactly one main step", () => {
    const placed = REVIEW_STEPS.flatMap((s) => s.views);
    expect(new Set(placed).size).toBe(placed.length);
    expect([...placed].sort()).toEqual(VIEWS.map((v) => v.id).sort());
  });

  it("finds the step a view belongs to", () => {
    expect(stepOfView("protocol")?.id).toBe("setup");
    expect(stepOfView("tables")?.id).toBe("extracting");
    expect(stepOfView("audit")?.id).toBe("completed");
    expect(stepOfView("nope")).toBeNull();
  });

  it("derives one state per step from the project status", () => {
    expect(stepStates("screening")).toEqual([
      "done",
      "done",
      "current",
      "todo",
      "todo",
      "todo",
      "todo",
      "todo",
    ]);
    expect(stepStates("completed").every((s) => s === "done")).toBe(true);
  });

  it("marks the flagged step when the project needs attention", () => {
    expect(currentStepIndex("needs_attention", "fulltext")).toBe(3);
    expect(stepStates("needs_attention", "fulltext")[3]).toBe("attention");
    expect(stepStates("needs_attention", null).every((s) => s === "todo")).toBe(true);
  });

  it("picks a neighbour when the active view is closed", () => {
    expect(nextAfterClose(["protocol", "search", "papers"], "search", "search")).toBe("papers");
    expect(nextAfterClose(["protocol", "search"], "search", "search")).toBe("protocol");
    expect(nextAfterClose(["search"], "search", "search")).toBeNull();
    expect(nextAfterClose(["protocol", "search"], "protocol", "search")).toBe("search");
  });

  it("matches palette queries word by word", () => {
    expect(matchesQuery("Open Evidence table", "table evid")).toBe(true);
    expect(matchesQuery("Open Evidence table", "report")).toBe(false);
    expect(matchesQuery("anything", "  ")).toBe(true);
  });
});

describe("palette ranking", () => {
  it("prefers label matches over keyword matches", async () => {
    const { rankQuery } = await import("./steps");
    expect(rankQuery("Papers", "papers")).toBe(0);
    expect(rankQuery("Evidence table", "table")).toBe(1);
    expect(rankQuery("Search", "papers")).toBe(2);
  });
});
