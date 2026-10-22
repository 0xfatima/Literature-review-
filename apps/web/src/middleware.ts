import { NextResponse, type NextRequest } from "next/server";

/** UX-only gate: redirects when there is no session cookie at all. It does NOT authorise anything.
 *  Real authentication/authorisation happens in the API on every request (a stale cookie gets 401 there).
 *  The requested page is carried in ?next= so the user lands back on it after signing in. */
export function middleware(req: NextRequest) {
  const name = process.env.NEXT_PUBLIC_SESSION_COOKIE_NAME ?? "srs_session";
  if (req.cookies.get(name)) return NextResponse.next();
  return NextResponse.redirect(
    loginUrl(req.nextUrl.pathname + req.nextUrl.search, publicOrigin(req.headers, req.nextUrl.origin)),
  );
}

const HOST = /^[A-Za-z0-9.-]+(:\d{1,5})?$/;

/** The origin the BROWSER used. Behind the edge proxy, req.url carries the web server's internal origin
 *  (e.g. http://web:3000 or :3001), and redirecting there sends users to an unreachable host. The proxy forwards
 *  the original Host and scheme; both are validated, and anything malformed falls back to Next's own view.
 *  (Next.js requires absolute Location URLs in middleware, so a relative redirect is not an option.) */
export function publicOrigin(headers: Headers, fallback: string): string {
  const host = headers.get("x-forwarded-host") ?? headers.get("host");
  const proto = headers.get("x-forwarded-proto") ?? new URL(fallback).protocol.replace(":", "");
  if (!host || !HOST.test(host) || (proto !== "http" && proto !== "https")) return fallback;
  return `${proto}://${host}`;
}

export function loginUrl(requested: string, origin: string): URL {
  const url = new URL("/login", origin);
  url.searchParams.set("next", requested);
  return url;
}

export const config = { matcher: ["/projects/:path*"] };
