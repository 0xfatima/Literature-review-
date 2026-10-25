import { describe, expect, it } from "vitest";
import { cellText, formatValue, highlight, parseInput } from "@/lib/extraction";
import type { EvidenceItem } from "@/lib/types";

const base: EvidenceItem = {
  id: "e1",
  paper_id: "p1",
  field_key: "n",
  status: "found",
  value: 120,
  excerpt: "enrolled 120 adults",
  document_page_id: "pg1",
  document_id: "d1",
  page_no: 4,
  reviewer_id: null,
  model_run_id: "m1",
  note: null,
  origin: "ai",
  review_status: "unreviewed",
  extraction_version: "extraction-v1",
  pages_inspected: [1, 2, 3, 4],
  validation: [],
  row_version: 1,
  created_at: "",
  updated_at: "",
};

describe("evidence table helpers", () => {
  it("formats values without inventing any", () => {
    expect(formatValue(null)).toBe("");
    expect(formatValue(true)).toBe("Yes");
    expect(formatValue(["A", "B"])).toBe("A; B");
    expect(formatValue({ text: "120" })).toBe("120");
  });

  it("spells out status, page and review state", () => {
    expect(cellText(base)).toEqual({ primary: "120", secondary: "p. 4 · AI · needs review" });
    expect(cellText({ ...base, review_status: "approved" }).secondary).toBe("p. 4 · Approved");
    const nl = cellText({ ...base, status: "not_located", value: null, page_no: null });
    expect(nl.primary).toBe("Not found in processed text");
    expect(nl.secondary).toContain("4 page(s) inspected");
    expect(cellText({ ...base, status: "unavailable", origin: "system" }).primary).toBe("No full text");
    expect(cellText(undefined).secondary).toBe("Not extracted");
  });

  it("highlights only an exact excerpt", () => {
    expect(highlight("We enrolled 120 adults here", "enrolled 120 adults")).toEqual([
      "We ",
      "enrolled 120 adults",
      " here",
    ]);
    expect(highlight("text", "missing")).toBeNull();
    expect(highlight("text", null)).toBeNull();
  });

  it("parses edits to the field type or reports an error", () => {
    expect(parseInput({ field_key: "n", label: "N", type: "number" }, "1,234")).toEqual({ value: 1234 });
    expect(parseInput({ field_key: "n", label: "N", type: "number" }, "about ten")).toHaveProperty("error");
    const choice = { field_key: "d", label: "D", type: "choice" as const, options: ["RCT"] };
    expect(parseInput(choice, "RCT")).toEqual({ value: "RCT" });
    expect(parseInput(choice, "Cohort")).toHaveProperty("error");
    expect(parseInput({ field_key: "b", label: "B", type: "boolean" }, "no")).toEqual({ value: false });
  });
});
