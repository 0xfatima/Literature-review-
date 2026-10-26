#!/usr/bin/env python3
"""Post-deployment smoke test (Phase 15). Standard library only, so it runs on any operator machine or CI runner.

    python3 scripts/smoke.py --base https://reviews.example.org            # full check (creates a throwaway user)
    python3 scripts/smoke.py --base https://localhost --insecure            # self-signed certificate (staging/CI)
    python3 scripts/smoke.py --base ... --marker create|verify|delete --marker-file marker.json   # restore drill

What a pass proves: the edge proxy serves the web app and the API over the configured scheme (HSTS on https), the API
reaches PostgreSQL (sign-up, project CRUD, audit log), Redis + the worker + the admission pump run jobs end to end
(a ping job reaches "succeeded"), uploads are stored and parsed by the worker (marker mode), owner isolation holds,
and operator endpoints are not exposed. Exit code 0 = every check passed; every check is printed.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
import zlib
from typing import Any

ap = argparse.ArgumentParser()
ap.add_argument("--base", required=True, help="public base URL, e.g. https://reviews.example.org")
ap.add_argument("--insecure", action="store_true", help="accept a self-signed certificate (staging/CI only)")
ap.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for background work")
ap.add_argument("--marker", choices=["create", "verify", "delete"], help="backup/restore drill step")
ap.add_argument("--marker-file", default="smoke-marker.json")
ap.add_argument("--api-only", action="store_true", help="target an API process directly (skip web/proxy checks)")
args = ap.parse_args()
BASE = args.base.rstrip("/")
CTX = ssl._create_unverified_context() if args.insecure else ssl.create_default_context()  # noqa: S323
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    if not ok:
        FAILS.append(name)
    return ok


class Client:
    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), urllib.request.HTTPSHandler(context=CTX)
        )

    def call(self, method: str, path: str, body: Any = None, *, raw: bytes | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, Any, dict[str, str]]:
        h = {"X-SRS-CSRF": "1", "Origin": BASE, **(headers or {})}
        data = raw
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)  # noqa: S310
        try:
            with self.op.open(req, timeout=30) as r:
                payload = r.read()
                status, hdrs = r.status, {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            payload, status, hdrs = e.read(), e.code, {k.lower(): v for k, v in e.headers.items()}
        try:
            parsed: Any = json.loads(payload) if payload else None
        except ValueError:
            parsed = payload[:200]
        return status, parsed, hdrs


def tiny_pdf(text: str) -> bytes:
    """A valid one-page PDF with real text (no third-party library)."""
    stream = zlib.compress(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def signed_up() -> tuple[Client, str, str]:
    c = Client()
    email, pw = f"smoke-{uuid.uuid4().hex[:12]}@example.invalid", f"smoke-{uuid.uuid4().hex}"
    s, _, _ = c.call("POST", "/api/v1/auth/register", {"email": email, "password": pw})
    check("register a throwaway user", s == 201, f"HTTP {s}")
    s, _, _ = c.call("POST", "/api/v1/auth/login", {"email": email, "password": pw})
    check("sign in", s == 200, f"HTTP {s}")
    return c, email, pw


def wait(fn, done, what: str):
    t0 = time.monotonic()
    v = None
    while time.monotonic() - t0 < args.timeout:
        v = fn()
        if done(v):
            return v, round(time.monotonic() - t0, 1)
        time.sleep(1)
    check(what, False, f"timed out after {args.timeout}s; last={str(v)[:200]}")
    return v, None


def platform_checks() -> None:
    anon = Client()
    s, _, h = anon.call("GET", "/healthz")
    check("proxy -> API liveness (/healthz)", s == 200, f"HTTP {s}")
    if args.api_only:
        print("SKIP  web/proxy checks (--api-only)")
    elif BASE.startswith("https://"):
        check("HSTS header present", "max-age" in h.get("strict-transport-security", ""), h.get("strict-transport-security", "missing"))
    if not args.api_only:
        s, _, _ = anon.call("GET", "/login")
        check("proxy -> web app (/login)", s == 200, f"HTTP {s}")
    s, body, h = anon.call("GET", "/api/v1/auth/me")
    check("API answers anonymous requests with 401 JSON", s == 401 and isinstance(body, dict), f"HTTP {s}")
    check("API responses are not cacheable", "no-store" in h.get("cache-control", ""), h.get("cache-control", ""))
    s, _, _ = anon.call("GET", "/internal/metrics")
    check("operator metrics are not available without the token", s == 404, f"HTTP {s}")


def full() -> None:
    platform_checks()
    c, _, _ = signed_up()
    s, p, _ = c.call("POST", "/api/v1/projects", {"title": "Smoke test (safe to delete)", "topic": "smoke"})
    if not check("create a project (PostgreSQL write)", s == 201, f"HTTP {s}"):
        return
    pid = p["id"]
    s, j, _ = c.call("POST", "/api/v1/jobs/ping", {"project_id": pid, "delay_s": 1},
                     headers={"Idempotency-Key": uuid.uuid4().hex})
    if check("enqueue a background job", s == 202, f"HTTP {s}"):
        job, took = wait(lambda: c.call("GET", f"/api/v1/jobs/{j['id']}")[1],
                         lambda v: isinstance(v, dict) and v.get("status") in ("succeeded", "failed", "cancelled"),
                         "background job finished")
        if took is not None:
            check("broker + worker + admission pump ran the job", job["status"] == "succeeded", f"{job['status']} in {took}s")
    s, ev, _ = c.call("GET", f"/api/v1/projects/{pid}/audit-events?limit=10")
    check("audit log records the project", s == 200 and any(e["action"] == "project.created" for e in ev["items"]), f"HTTP {s}")
    other, _, _ = signed_up()
    s, _, _ = other.call("GET", f"/api/v1/projects/{pid}")
    check("another user cannot read the project (404)", s == 404, f"HTTP {s}")
    s, _, _ = c.call("DELETE", f"/api/v1/projects/{pid}")
    check("delete the smoke project", s == 204, f"HTTP {s}")


def marker() -> None:
    if args.marker == "create":
        c, email, pw = signed_up()
        s, p, _ = c.call("POST", "/api/v1/projects", {"title": "Restore drill marker", "topic": "restore drill"})
        check("create marker project", s == 201, f"HTTP {s}")
        s, d, _ = c.call("POST", f"/api/v1/projects/{p['id']}/documents", raw=tiny_pdf("restore drill marker page"),
                         headers={"Content-Type": "application/pdf", "X-Filename": "marker.pdf"})
        check("upload marker PDF", s == 202, f"HTTP {s}")
        doc, _ = wait(lambda: c.call("GET", f"/api/v1/projects/{p['id']}/documents/{d['id']}")[1],
                      lambda v: isinstance(v, dict) and v.get("parse_status") not in ("queued", "processing"),
                      "marker PDF processed")
        check("marker PDF parsed by the worker", isinstance(doc, dict) and doc.get("parse_status") == "parsed",
              str((doc or {}).get("parse_status")))
        with open(args.marker_file, "w") as f:
            json.dump({"email": email, "password": pw, "project_id": p["id"], "document_id": d["id"]}, f)
        return
    m = json.load(open(args.marker_file))
    c = Client()
    s, _, _ = c.call("POST", "/api/v1/auth/login", {"email": m["email"], "password": m["password"]})
    check("marker user can sign in", s == 200, f"HTTP {s}")
    if args.marker == "delete":
        s, _, _ = c.call("DELETE", f"/api/v1/projects/{m['project_id']}")
        check("delete marker project (simulated data loss)", s == 204, f"HTTP {s}")
        s, _, _ = c.call("GET", f"/api/v1/projects/{m['project_id']}")
        check("marker project is gone before restore", s == 404, f"HTTP {s}")
        return
    s, p, _ = c.call("GET", f"/api/v1/projects/{m['project_id']}")
    check("marker project restored", s == 200 and p.get("title") == "Restore drill marker", f"HTTP {s}")
    s, raw, h = c.call("GET", f"/api/v1/projects/{m['project_id']}/documents/{m['document_id']}/download")
    check("marker PDF file restored from storage", s == 200 and h.get("content-type", "").startswith("application/pdf"),
          f"HTTP {s}")
    s, ev, _ = c.call("GET", f"/api/v1/projects/{m['project_id']}/audit-events?limit=50")
    check("marker audit history restored", s == 200 and any(e["action"] == "project.created" for e in ev["items"]))


if __name__ == "__main__":
    marker() if args.marker else full()
    print(f"\nsmoke: {'ALL PASSED' if not FAILS else str(len(FAILS)) + ' FAILED: ' + '; '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
