import { describe, expect, it } from "vitest";
import { acceptBlocker, gapSummary, humanize, undecidedDomains } from "@/lib/synthesis";
import type { AppraisalFramework, FieldSignal, SynthesisStatement } from "@/lib/types";

const st = (over: Partial<SynthesisStatement>): SynthesisStatement => ({
  id: "s",
  kind: "finding",
  text: "t",
  group: null,
  citations: [],
  appraisals: [],
  claimed_certainty: null,
  certainty: "uncertain",
  certainty_reasons: [],
  origin: "system",
  review: "accepted",
  warnings: [],
  stale: false,
  stale_reasons: [],
  ...over,
});

describe("synthesis helpers", () => {
  it("blocks acceptance until every statement is reviewed and current", () => {
    expect(acceptBlocker([st({ review: "unreviewed" })])).toMatch(/Review every/);
    expect(acceptBlocker([st({ review: "rejected" })])).toMatch(/at least one/);
    expect(acceptBlocker([st({ stale: true })])).toMatch(/changed/);
    expect(acceptBlocker([st({}), st({ review: "rejected", stale: true })])).toBeNull();
  });

  it("keeps gap reasons apart", () => {
    const sig: FieldSignal = {
      field_key: "n",
      label: "N",
      type: "number",
      pattern: "single_study",
      studies_with_value: 1,
      values: [],
      gaps: { not_located: ["a"], unavailable: ["b", "c"], ambiguous: [], not_reviewed: ["d"] },
    };
    expect(gapSummary(sig)).toBe("1 not found in processed text · 2 without full text · 1 not yet reviewed");
  });

  it("lists domains without a reviewer judgement", () => {
    const fw = {
      domains: [
        { key: "a", label: "A", prompt: "" },
        { key: "b", label: "B", prompt: "" },
      ],
    } as AppraisalFramework;
    expect(
      undecidedDomains(fw, {
        a: { decided_by: "u" },
        b: {
          ai_suggestion: {
            judgement: "low",
            justification: null,
            excerpt: null,
            document_page_id: null,
            page_no: null,
            supported: false,
            model_run_id: null,
            prompt_version: "v",
          },
        },
      }),
    ).toEqual(["B"]);
    expect(humanize("some_concerns")).toBe("some concerns");
  });
});
