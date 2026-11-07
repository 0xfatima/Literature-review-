import { describe, expect, it } from "vitest";
import { stepNav } from "./stepnav";

const stages = [
  { key: "protocol", status: "done", detail: "Version 1 approved" },
  { key: "search", status: "in_progress", detail: "Searching" },
];

describe("stepNav", () => {
  it("enables Next only when the step is done", () => {
    expect(stepNav("protocol", stages)).toMatchObject({ prev: null, next: "search", complete: true });
    expect(stepNav("search", stages)).toMatchObject({ prev: "protocol", next: "screening", complete: false });
  });
  it("places side views on their step and ends at the report", () => {
    expect(stepNav("papers", stages).next).toBe("screening");
    expect(stepNav("report", stages)).toMatchObject({ next: null, complete: false });
    expect(stepNav("audit", stages).complete).toBe(true);
  });
});
