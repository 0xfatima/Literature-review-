import { describe, expect, it } from "vitest";
import {
  citeLabel,
  decisionProblem,
  FT_STATUS_LABELS,
  FT_STATUS_ORDER,
  groupingProblem,
  groupReasons,
} from "@/lib/fulltext";
import type { FtReason } from "@/lib/types";

describe("full-text decisions", () => {
  it("requires a reason to exclude, and a note for 'other'", () => {
    expect(decisionProblem("include", "", "")).toBeNull();
    expect(decisionProblem("uncertain", "", "")).toBeNull();
    expect(decisionProblem("exclude", "", "")).toMatch(/reason/);
    expect(decisionProblem("exclude", "other", " ")).toMatch(/note/);
    expect(decisionProblem("exclude", "other", "Conference abstract only")).toBeNull();
    expect(decisionProblem("exclude", "wrong_population", "")).toBeNull();
  });

  it("groups reasons with the protocol's criteria first and drops empty groups", () => {
    const reasons: FtReason[] = [
      { code: "wrong_population", label: "Wrong population", group: "generic" },
      { code: "exclusion:a", label: "Meets exclusion: adults", group: "exclusion_criterion" },
    ];
    const g = groupReasons(reasons);
    expect(g.map((x) => x.label)).toEqual(["Your exclusion criteria", "Standard reasons"]);
    expect(g[0]!.options[0]!.code).toBe("exclusion:a");
  });

  it("labels every status in words", () => {
    for (const s of FT_STATUS_ORDER) expect(FT_STATUS_LABELS[s].length).toBeGreaterThan(3);
  });
});

describe("study grouping", () => {
  it("needs two or more reports", () => {
    expect(groupingProblem(["a"], "")).toMatch(/two/);
    expect(groupingProblem(["a", "b"], "Smith 2020")).toBeNull();
    expect(groupingProblem(["a", "b"], "x".repeat(201))).toMatch(/too long/);
  });

  it("builds a short citation label", () => {
    expect(citeLabel({ first_author: "Smith", author_count: 3, year: 2020 })).toBe("Smith et al. (2020)");
    expect(citeLabel({ first_author: "Lee", author_count: 1, year: null })).toBe("Lee");
    expect(citeLabel({ first_author: null, author_count: 0, year: 2019 })).toBe("Unknown author (2019)");
  });
});
