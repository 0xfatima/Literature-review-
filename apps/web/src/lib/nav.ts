/** Where to go after sign-in. Only same-origin app paths are allowed (no open redirect to another site). */
export function safeNext(raw: string | null | undefined, fallback = "/projects"): string {
  if (!raw) return fallback;
  // Must be an absolute path on this origin: "/x". Reject "//host", "/\host" and anything with a scheme.
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return fallback;
  if (/[\u0000-\u001f]/.test(raw)) return fallback;
  if (raw.startsWith("/login") || raw.startsWith("/register")) return fallback; // avoid loops
  return raw;
}
