import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, toApiError } from "./api";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(status: number, body: unknown, headers: Record<string, string> = {}) {
  const fn = vi.fn(async () => new Response(body === undefined ? null : JSON.stringify(body), { status, headers }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("apiFetch", () => {
  it("sends the CSRF header, JSON content type and same-origin credentials", async () => {
    const fn = stubFetch(200, { ok: true });
    await apiFetch("/projects", { method: "POST", body: "{}" });
    const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/projects");
    expect(init.credentials).toBe("same-origin");
    const h = new Headers(init.headers);
    expect(h.get("X-SRS-CSRF")).toBe("1");
    expect(h.get("Content-Type")).toBe("application/json");
  });

  it("returns undefined for 204", async () => {
    stubFetch(204, undefined);
    expect(await apiFetch("/auth/logout", { method: "POST" })).toBeUndefined();
  });

  it("maps the backend error envelope to ApiError", async () => {
    stubFetch(409, { error: { code: "email_taken", message: "exists", details: null, correlation_id: "c-1" } });
    await expect(apiFetch("/auth/register")).rejects.toMatchObject({ status: 409, code: "email_taken", correlationId: "c-1" });
  });

  it("falls back to a generic message for non-envelope failures", () => {
    const e = toApiError(502, null, "hdr-corr");
    expect(e).toBeInstanceOf(ApiError);
    expect(e.code).toBe("http_error");
    expect(e.correlationId).toBe("hdr-corr");
    expect(e.message).not.toMatch(/stack|trace/i);
  });
});
