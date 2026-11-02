import { describe, expect, it } from "vitest";
import { cellText, formatValue, highlight, parseInput, questionCoverage } from "@/lib/extraction";
import type { EvidenceItem, FieldCoverage } from "@/lib/types";

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

describe("questionCoverage", () => {
  const f = (o: Partial<FieldCoverage>): FieldCoverage => ({
    field_key: "k",
    label: "K",
    included_papers: 2,
    found: 0,
    not_located: 0,
    unavailable: 0,
    ambiguous: 0,
    pending: 0,
    ...o,
  });
  it("needs mapped fields and included papers", () => {
    expect(questionCoverage([], 3)).toBe("no_fields");
    expect(questionCoverage([f({ found: 1 })], 0)).toBe("not_assessed");
  });
  it("answered only when every value was found", () => {
    expect(questionCoverage([f({ found: 2 }), f({ found: 2 })], 2)).toBe("answered");
    expect(questionCoverage([f({ found: 2 }), f({ found: 1, not_located: 1 })], 2)).toBe("partial");
    expect(questionCoverage([f({ found: 1, pending: 1 })], 2)).toBe("partial");
  });
  it("never confuses missing full text with absent information", () => {
    expect(questionCoverage([f({ unavailable: 2 })], 2)).toBe("no_full_text");
    expect(questionCoverage([f({ not_located: 1, unavailable: 1 })], 2)).toBe("not_found");
  });
  it("is not assessed while nothing has been extracted", () => {
    expect(questionCoverage([f({ pending: 2 })], 2)).toBe("not_assessed");
    expect(questionCoverage([f({ pending: 1, not_located: 1 })], 2)).toBe("not_assessed");
  });
});
