import { describe, expect, it } from "vitest";
import { friendlyAiError } from "./screening";

describe("friendlyAiError", () => {
  it("explains an interrupted worker in plain words", () => {
    expect(friendlyAiError("Worker lost; retry attempts exhausted.")).toMatch(/interrupted/);
  });
  it("passes other errors through", () => {
    expect(friendlyAiError("Something else")).toBe("Something else");
  });
});
