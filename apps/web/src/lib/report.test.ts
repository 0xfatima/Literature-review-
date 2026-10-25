import { describe, expect, it } from "vitest";
import { displayText, isEditable, isPlaceholder, issuesFor } from "@/lib/report";
import type { ReportIssue } from "@/lib/types";

describe("report helpers", () => {
  it("renders citation markers as keys", () => {
    expect(displayText("Benefit [@smith2020] [@lee2021a].")).toBe("Benefit [smith2020] [lee2021a].");
  });

  it("orders a block's issues with errors first", () => {
    const issues: ReportIssue[] = [
      { code: "w", severity: "warning", section: "s", block_id: "b1", message: "" },
      { code: "e", severity: "error", section: "s", block_id: "b1", message: "" },
      { code: "x", severity: "error", section: "s", block_id: "b2", message: "" },
    ];
    expect(issuesFor(issues, "b1").map((i) => i.code)).toEqual(["e", "w"]);
  });

  it("knows editable blocks and placeholders", () => {
    expect(isEditable({ id: "t", type: "references" })).toBe(false);
    expect(isEditable({ id: "p", type: "paragraph", text: "", origin: "generated", source: "x" })).toBe(true);
    expect(isPlaceholder("[[To be written by the authors: background]]")).toBe(true);
    expect(isPlaceholder("Plain [@key] text")).toBe(false);
  });
});
