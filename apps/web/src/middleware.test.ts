import { describe, expect, it } from "vitest";
import { loginUrl, publicOrigin } from "./middleware";

const h = (o: Record<string, string>) => new Headers(o);
const INTERNAL = "http://web:3000";

describe("publicOrigin", () => {
  it("uses the browser-facing host and scheme forwarded by the edge proxy, not the internal origin", () => {
    expect(publicOrigin(h({ host: "localhost:3000", "x-forwarded-proto": "http" }), INTERNAL)).toBe(
      "http://localhost:3000",
    );
    expect(
      publicOrigin(h({ "x-forwarded-host": "review.example.org", "x-forwarded-proto": "https" }), INTERNAL),
    ).toBe("https://review.example.org");
  });
  it.each([
    [{ host: "evil.example/path" }],
    [{ host: "a b" }],
    [{ host: "localhost:3000", "x-forwarded-proto": "javascript" }],
    [{}],
  ])("falls back to Next's own origin for malformed input %j", (headers) => {
    expect(publicOrigin(h(headers as Record<string, string>), INTERNAL)).toBe(INTERNAL);
  });
});

describe("loginUrl", () => {
  it("carries the requested page in ?next=", () => {
    const u = loginUrl("/projects/abc?c=def", "http://localhost:3000");
    expect(u.toString()).toBe("http://localhost:3000/login?next=%2Fprojects%2Fabc%3Fc%3Ddef");
  });
});
