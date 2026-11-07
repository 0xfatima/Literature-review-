import { describe, expect, it } from "vitest";
import { pricingLabel, sortModels, type LLMModel } from "./account";

const m = (id: string, pricing: "free" | "paid"): LLMModel => ({ id, label: id, pricing, note: "" });

describe("sortModels", () => {
  it("puts the default first, then free models", () => {
    const out = sortModels([m("b-paid", "paid"), m("c-free", "free"), m("a-default", "paid")], "a-default");
    expect(out.map((x) => x.id)).toEqual(["a-default", "c-free", "b-paid"]);
  });
  it("labels pricing", () => {
    expect(pricingLabel("free")).toBe("Free");
    expect(pricingLabel(null)).toBe("");
  });
});
