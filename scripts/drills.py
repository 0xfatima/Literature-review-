#!/usr/bin/env python3
"""Live-stack drills (stdlib only). Requires the compose stack (docker compose up) and Docker CLI.

Each drill prints a JSON evidence record and returns PASS/FAIL. Usage:
    python scripts/drills.py list
    python scripts/drills.py run worker-kill broker-outage ...      (or: all)
Timing overrides for crash drills are applied through a temporary compose override file, never by editing .env.
"""
import http.cookiejar, json, os, pathlib, subprocess, sys, tempfile, time, urllib.error, urllib.request, uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = os.environ.get("DRILL_API", "http://localhost:8000")
WEB = os.environ.get("DRILL_WEB", "http://localhost:3000")
ORIGIN = "http://localhost:3000"
PASSWORD = "a-long-drill-password-1"
EVIDENCE: dict = {}
OVERRIDE = pathlib.Path(tempfile.gettempdir()) / "srs-drill-override.yml"


def compose(*args, check=True, input_text=None):
    cmd = ["docker", "compose", "-f", str(ROOT / "docker-compose.yml")]
    if OVERRIDE.exists():
        cmd += ["-f", str(OVERRIDE)]
    r = subprocess.run(cmd + list(args), cwd=ROOT, capture_output=True, text=True, input=input_text, timeout=300)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {r.stderr.strip()[:400]}")
    return r


def psql(sql):
    return compose("exec", "-T", "postgres", "psql", "-U", "srs", "-d", "srs", "-At", "-c", sql, check=False)


def _lower(headers):
    # HTTP header names are case-insensitive and uvicorn/h11 sends them lowercase ("retry-after").
    return {k.lower(): v for k, v in headers.items()}


def reset_rate_limits():
    """Delete every rate-limit bucket (keys rl:*) in the stack's Redis. The IP drills deliberately exhaust the
    bucket of the CI host's address; without this, later drills that log in from the same address get 429.
    Drill harness only: never part of the application."""
    lua = "local n=0 for _,k in ipairs(redis.call('KEYS', ARGV[1])) do redis.call('DEL', k) n=n+1 end return n"
    r = compose("exec", "-T", "redis", "redis-cli", "EVAL", lua, "0", "rl:*", check=False)
    if r.returncode != 0:
        raise RuntimeError(f"could not reset rate limits: {r.stderr.strip()[:200]}")


class Client:
    def __init__(self, base=API, extra_headers=None):
        self.base, self.jar = base, http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.headers = {"Content-Type": "application/json", "Origin": ORIGIN, "X-SRS-CSRF": "1", **(extra_headers or {})}

    def call(self, method, path, body=None, headers=None, csrf=True):
        h = dict(self.headers); h.update(headers or {})
        if not csrf:
            h.pop("X-SRS-CSRF", None)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with self.op.open(req, timeout=30) as r:
                raw = r.read(); return r.status, (json.loads(raw) if raw else None), _lower(r.headers)
        except urllib.error.HTTPError as e:
            raw = e.read(); 
            try: parsed = json.loads(raw)
            except Exception: parsed = {"raw": raw.decode(errors="replace")[:200]}
            return e.code, parsed, _lower(e.headers)

    def cookie(self, name="srs_session"):
        return next((c.value for c in self.jar if c.name == name), None)


def new_user(base=API):
    c = Client(base); email = f"drill-{uuid.uuid4().hex[:10]}@example.test"
    s1, _, _ = c.call("POST", "/api/v1/auth/register", {"email": email, "password": PASSWORD})
    assert s1 == 201, f"register failed {s1} (raise RATE_LIMIT_REGISTER_PER_HOUR for validation runs)"
    for _ in range(3):  # our own login limiter may be warm from earlier drills: honour Retry-After
        s2, _, h = c.call("POST", "/api/v1/auth/login", {"email": email, "password": PASSWORD})
        if s2 != 429: break
        time.sleep(int(h.get("retry-after", "5")) + 1)
    assert s2 == 200, f"login failed {s2}"
    return c, email


def use_fast_timers():
    OVERRIDE.write_text("""services:
  api:    {environment: {JOB_STALE_AFTER_S: "20", JOB_SWEEP_INTERVAL_S: "5", JOB_REQUEUE_QUEUED_AFTER_S: "10"}}
  worker: {environment: {JOB_STALE_AFTER_S: "20", JOB_SWEEP_INTERVAL_S: "5", JOB_REQUEUE_QUEUED_AFTER_S: "10"}}
""")
    compose("up", "-d", "--force-recreate", "--wait", "api", "worker")


def poll(fn, ok, timeout, every=1.0):
    t0 = time.time(); last = None
    while time.time() - t0 < timeout:
        last = fn()
        if ok(last): return last, round(time.time() - t0, 1)
        time.sleep(every)
    return last, None


def drill_worker_kill():
    use_fast_timers()
    c, _ = new_user(); tl = []
    mark = lambda ev, **kw: tl.append({"t": time.strftime("%H:%M:%S"), "event": ev, **kw})
    s, job, _ = c.call("POST", "/api/v1/jobs/ping", {"delay_s": 60}, {"Idempotency-Key": uuid.uuid4().hex})
    assert s == 202, (s, job); jid = job["id"]; mark("enqueued", job_id=jid, status=job["status"])
    j, _ = poll(lambda: c.call("GET", f"/api/v1/jobs/{jid}")[1], lambda j: j["status"] == "running" and j["progress"] > 0, 40)
    assert j and j["status"] == "running", f"job never started: {j}"; mark("running", attempts=j["attempts"], progress=j["progress"])
    compose("kill", "-s", "SIGKILL", "worker"); mark("worker SIGKILLed")
    time.sleep(8); j = c.call("GET", f"/api/v1/jobs/{jid}")[1]; mark("after kill, before stale window", status=j["status"], progress=j["progress"])
    compose("up", "-d", "worker"); mark("worker restarted")
    j, took = poll(lambda: c.call("GET", f"/api/v1/jobs/{jid}")[1], lambda j: j["status"] in ("succeeded", "failed"), 240, 2)
    mark("terminal", status=j and j["status"], attempts=j and j["attempts"], seconds_after_restart=took)
    ev = psql(f"select action, count(*) from audit_events where entity_id='{jid}' group by action order by action").stdout.strip().splitlines()
    row = psql(f"select status, attempts, progress, result::text from jobs where id='{jid}'").stdout.strip()
    EVIDENCE["worker-kill"] = {"timeline": tl, "audit_actions_for_job": ev, "jobs_row": row}
    ok = (j and j["status"] == "succeeded" and j["attempts"] == 2 and j["result"] == {"pong": True}
          and any(l.startswith("job.recovered_queued|1") for l in ev))
    return bool(ok)


def drill_broker_outage():
    use_fast_timers()
    c, _ = new_user()
    compose("stop", "redis")
    try:
        t0 = time.time(); s, job, _ = c.call("POST", "/api/v1/jobs/ping", {"delay_s": 1}, {"Idempotency-Key": uuid.uuid4().hex})
        elapsed = round(time.time() - t0, 2)
        row = psql(f"select status, enqueued_at is null from jobs where id='{job.get('id')}'").stdout.strip()
    finally:
        compose("start", "redis")
    j, took = poll(lambda: c.call("GET", f"/api/v1/jobs/{job['id']}")[1], lambda j: j["status"] == "succeeded", 120, 2)
    EVIDENCE["broker-outage"] = {"http_status_during_outage": s, "request_seconds": elapsed, "db_row_during_outage(status|enqueued_at_is_null)": row,
                                 "final_status": j and j["status"], "seconds_to_recover": took}
    return s == 202 and row == "queued|t" and elapsed < 15 and bool(j and j["status"] == "succeeded")


def _drill_ip_limit_body():
    # Through the edge proxy: forged X-Forwarded-For entries must NOT evade the per-IP bucket,
    # and the bucket must be per real client (not one global bucket) - the latter is only observable with 2 real IPs (manual).
    c = Client(WEB); codes = []
    limit = int(os.environ.get("DRILL_LOGIN_LIMIT", "10"))
    for i in range(limit + 4):
        s, _, _ = c.call("POST", "/api/v1/auth/login", {"email": f"nobody-{uuid.uuid4().hex[:8]}@example.test", "password": "wrong-password-123"},
                         {"X-Forwarded-For": f"10.{i}.{i}.{i}"})
        codes.append(s)
    EVIDENCE["ip-limit"] = {"status_codes": codes, "note": "expect 401s then 429s; first 429 should appear after ~10 attempts"}
    return 429 in codes and codes.index(429) <= limit + 1


def drill_session_revocation():
    c, email = new_user(); tok = c.cookie()
    assert c.call("GET", "/api/v1/auth/me")[0] == 200
    assert c.call("POST", "/api/v1/auth/logout")[0] == 204
    replay = Client(); replay.jar.set_cookie(http.cookiejar.Cookie(0, "srs_session", tok, None, False, "localhost.local", False, False, "/", True, False, None, False, None, None, {}))
    after_logout = replay.call("GET", "/api/v1/auth/me")[0]
    c2, email2 = new_user(); tok2 = c2.cookie()
    r = compose("run", "--rm", "-T", "api", "python", "-m", "app.cli", "set-password", email2, input_text="another-long-password-2\n", check=False)
    replay2 = Client(); replay2.jar.set_cookie(http.cookiejar.Cookie(0, "srs_session", tok2, None, False, "localhost.local", False, False, "/", True, False, None, False, None, None, {}))
    after_reset = replay2.call("GET", "/api/v1/auth/me")[0]
    relogin = Client().call("POST", "/api/v1/auth/login", {"email": email2, "password": "another-long-password-2"})[0]
    EVIDENCE["session-revocation"] = {"replay_after_logout": after_logout, "cli_exit": r.returncode, "replay_after_operator_reset": after_reset, "login_with_new_password": relogin}
    return after_logout == 401 and r.returncode == 0 and after_reset == 401 and relogin == 200


def drill_csrf():
    c, _ = new_user()
    no_hdr = c.call("POST", "/api/v1/projects", {"title": "t", "topic": "x"}, csrf=False)[0]
    bad_origin = c.call("POST", "/api/v1/projects", {"title": "t", "topic": "x"}, {"Origin": "https://evil.example"})[0]
    ok = c.call("POST", "/api/v1/projects", {"title": "t", "topic": "x"})[0]
    EVIDENCE["csrf"] = {"no_header": no_hdr, "evil_origin": bad_origin, "legit": ok}
    return (no_hdr, bad_origin, ok) == (403, 403, 201)


def drill_audit_immutable():
    new_user()
    out = {}
    for name, sql in [("update", "update audit_events set action='x'"), ("delete", "delete from audit_events"), ("truncate", "truncate audit_events")]:
        r = psql(sql); out[name] = {"rc": r.returncode, "stderr": r.stderr.strip()[:120]}
    n = psql("select count(*) from audit_events").stdout.strip()
    EVIDENCE["audit-immutable"] = {**out, "rows_remaining": n}
    return all(v["rc"] != 0 and "append-only" in v["stderr"] for v in out.values()) and int(n or 0) > 0


def drill_ownership():
    a, _ = new_user(); b, _ = new_user()
    pid = a.call("POST", "/api/v1/projects", {"title": "A-private", "topic": "secret"})[1]["id"]
    res = {"B_get": b.call("GET", f"/api/v1/projects/{pid}")[0], "B_patch": b.call("PATCH", f"/api/v1/projects/{pid}", {"expected_version": 1, "title": "x"})[0],
           "B_delete": b.call("DELETE", f"/api/v1/projects/{pid}")[0], "B_list_count": b.call("GET", "/api/v1/projects")[1]["total"],
           "B_missing": b.call("GET", f"/api/v1/projects/{uuid.uuid4()}")[0], "A_still_there": a.call("GET", f"/api/v1/projects/{pid}")[1]["title"]}
    EVIDENCE["ownership"] = res
    return res == {"B_get": 404, "B_patch": 404, "B_delete": 404, "B_list_count": 0, "B_missing": 404, "A_still_there": "A-private"}


def _drill_ip_isolation_body():
    """Is the per-IP bucket per REAL client, or one global bucket behind the edge proxy?
    Client 1 = the host (arrives at the edge proxy from the docker bridge gateway). Client 2 = a container on the
    compose network (arrives from a different address). Exhaust client 1's bucket; client 2 must still get 401.
    If the edge proxy does not set X-Forwarded-For, both look like one address and client 2 gets 429 -> FAIL."""
    limit = int(os.environ.get("DRILL_LOGIN_LIMIT", "10"))
    c1 = Client(WEB); codes1 = []
    for _ in range(limit + 3):
        codes1.append(c1.call("POST", "/api/v1/auth/login", {"email": f"iso-{uuid.uuid4().hex[:8]}@example.test", "password": "wrong-password-123"})[0])
    snippet = ("import json,urllib.request,urllib.error,uuid;"
               "r=urllib.request.Request('http://proxy/api/v1/auth/login',method='POST',"
               "data=json.dumps({'email':'iso2-'+uuid.uuid4().hex[:8]+'@example.test','password':'wrong-password-123'}).encode(),"
               "headers={'Content-Type':'application/json','X-SRS-CSRF':'1','Origin':'http://localhost:3000'})\n"
               "try: urllib.request.urlopen(r,timeout=10)\nexcept urllib.error.HTTPError as e: print(e.code)")
    r = compose("exec", "-T", "api", "python", "-c", snippet, check=False)
    code2 = (r.stdout.strip() or r.stderr.strip()[-80:])
    EVIDENCE["ip-isolation"] = {"client1_codes": codes1, "client2_code_from_other_address": code2}
    return 429 in codes1 and code2 == "401"



def drill_ip_limit():
    reset_rate_limits()
    try:
        return _drill_ip_limit_body()
    finally:
        reset_rate_limits()



def drill_ip_isolation():
    reset_rate_limits()
    try:
        return _drill_ip_isolation_body()
    finally:
        reset_rate_limits()


DRILLS = {"worker-kill": drill_worker_kill, "broker-outage": drill_broker_outage, "ip-limit": drill_ip_limit, "ip-isolation": drill_ip_isolation,
          "session-revocation": drill_session_revocation, "csrf": drill_csrf, "audit-immutable": drill_audit_immutable,
          "ownership": drill_ownership}


def main(argv):
    if not argv or argv[0] == "list":
        print("\n".join(DRILLS)); return 0
    names = list(DRILLS) if argv[1:] == ["all"] or len(argv) == 1 else argv[1:]
    results = {}
    # cheap, non-destructive drills first; the destructive ones (kill/stop) last
    order = sorted(names, key=lambda n: n in ("worker-kill", "broker-outage"))
    for n in order:
        try: results[n] = "PASS" if DRILLS[n]() else "FAIL"
        except Exception as e:  # noqa: BLE001
            results[n] = f"ERROR: {e}"
        print(f"{results[n]:6s} {n}")
    out = ROOT / "validation-evidence"; out.mkdir(exist_ok=True)
    (out / "drills-evidence.json").write_text(json.dumps({"results": results, "evidence": EVIDENCE}, indent=2))
    print(json.dumps(EVIDENCE, indent=2))
    return 0 if all(v == "PASS" for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
