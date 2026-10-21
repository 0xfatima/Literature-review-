import { NextResponse, type NextRequest } from "next/server";

/** UX-only gate: redirects when there is no session cookie at all. It does NOT authorise anything.
 *  Real authentication/authorisation happens in the API on every request (a stale cookie gets 401 there). */
export function middleware(req: NextRequest) {
  const name = process.env.NEXT_PUBLIC_SESSION_COOKIE_NAME ?? "srs_session";
  if (!req.cookies.get(name)) return NextResponse.redirect(new URL("/login", req.url));
  return NextResponse.next();
}
export const config = { matcher: ["/projects/:path*"] };
