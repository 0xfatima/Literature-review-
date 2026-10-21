/** Thin typed client for the FastAPI backend (same-origin via the Next.js /api rewrite). */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly details: unknown,
    public readonly correlationId: string | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-SRS-CSRF", "1"); // required by the backend for state-changing requests
  if (init.body !== undefined && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const res = await fetch(`/api/v1${path}`, { ...init, headers, credentials: "same-origin" });
  if (res.status === 204) return undefined as T;
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) throw toApiError(res.status, body, res.headers.get("x-correlation-id"));
  return body as T;
}

export function toApiError(status: number, body: unknown, headerCorrelationId: string | null = null): ApiError {
  const e = (body as { error?: { code?: string; message?: string; details?: unknown; correlation_id?: string | null } } | null)?.error;
  return new ApiError(
    status,
    e?.code ?? "http_error",
    e?.message ?? "Something went wrong. Please try again.",
    e?.details ?? null,
    e?.correlation_id ?? headerCorrelationId,
  );
}

export const json = (data: unknown): RequestInit => ({ body: JSON.stringify(data) });
