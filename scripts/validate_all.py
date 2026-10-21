#!/usr/bin/env python3
"""One-command Phase 1 validation with evidence capture (stdlib only).

    python scripts/validate_all.py                 # everything (needs network + Docker + Python 3.12 + Node 22)
    python scripts/validate_all.py --only env,locks,stdlib,static
    python scripts/validate_all.py --dry-run       # print the plan, run nothing

Outputs validation-evidence/<UTC timestamp>/{results.json,REPORT.md,<step>.log, junit/json files}.
Status meanings: PASS | FAIL | BLOCKED (a prerequisite step did not PASS) | NOT_RUN (filtered out / dry-run).
Exit code is 0 only if EVERY selected step is PASS. Nothing is ever reported as passing because it was skipped:
test steps fail if any test is skipped, errored, or if the executed count is below the expected minimum.
"""
import argparse, json, os, pathlib, re, subprocess, sys, time, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
EV = ROOT / "validation-evidence" / STAMP
VENV = ROOT / ".venv-validate"
PY = str(VENV / "bin" / "python")
PW = os.environ.get("POSTGRES_PASSWORD", "srs_validation_pw")
TEST_DB = f"postgresql+psycopg://srs:{PW}@localhost:5432/srs_test"
MIN_INTEGRATION, MIN_TOTAL_PYTEST, MIN_VITEST, EXPECTED_E2E = 50, 54, 4, 5  # from the repo inventory; raise when tests are added
STEPS: list[dict] = []


def step(id, title, cmd=None, cwd=ROOT, env=None, requires=(), pre=None, post=None, timeout=1800):
    STEPS.append(dict(id=id, title=title, cmd=cmd, cwd=cwd, env=env or {}, requires=list(requires), pre=pre, post=post, timeout=timeout))


# ---------- post-checks: return (ok, detail) ----------
def junit_counts(path):
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    t = f = e = s = 0
    integ = 0
    for su in suites:
        t += int(su.get("tests", 0)); f += int(su.get("failures", 0)); e += int(su.get("errors", 0)); s += int(su.get("skipped", 0))
    for tc in root.iter("testcase"):
        if "integration" in (tc.get("classname") or ""): integ += 1
    return t, f, e, s, integ


def post_pytest(_):
    t, f, e, s, integ = junit_counts(EV / "pytest.xml")
    ok = f == 0 and e == 0 and s == 0 and t >= MIN_TOTAL_PYTEST and integ >= MIN_INTEGRATION
    return ok, f"tests={t} failures={f} errors={e} skipped={s} integration_cases={integ} (need skipped=0, integration>={MIN_INTEGRATION}, total>={MIN_TOTAL_PYTEST})"


def post_vitest(_):
    t, f, e, s, _i = junit_counts(EV / "vitest.xml")
    return (f == 0 and e == 0 and s == 0 and t >= MIN_VITEST), f"tests={t} failures={f} errors={e} skipped={s}"


def post_playwright(_):
    st = json.loads((EV / "playwright.json").read_text())["stats"]
    ok = st["unexpected"] == 0 and st["skipped"] == 0 and st["flaky"] == 0 and st["expected"] >= EXPECTED_E2E
    return ok, f"expected(passed)={st['expected']} unexpected={st['unexpected']} skipped={st['skipped']} flaky={st['flaky']}"


def http_json(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:  # 401/503 etc. are valid answers we want to inspect
        try: return e.code, json.loads(e.read())
        except Exception: return e.code, None  # noqa: BLE001
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def post_stack(_):
    deadline = time.time() + 240; last = {}
    while time.time() < deadline:
        last = {
            "api_healthz": http_json("http://localhost:8000/healthz")[0], "api_readyz": http_json("http://localhost:8000/readyz")[0],
            "worker": http_json("http://localhost:8000/healthz/worker")[0], "web_via_proxy_readyz": http_json("http://localhost:3000/api/v1/auth/me")[0]}
        # /auth/me through the Next proxy must answer 401 JSON (proves proxy -> api); the other three must be 200
        if last["api_healthz"] == 200 and last["api_readyz"] == 200 and last["worker"] == 200 and last["web_via_proxy_readyz"] in (200, 401):
            alembic = subprocess.run(["docker", "compose", "run", "--rm", "migrate", "alembic", "current"], cwd=ROOT, capture_output=True, text=True)
            return "0001_baseline" in (alembic.stdout + alembic.stderr), f"health={last}; alembic current: {(alembic.stdout + alembic.stderr).strip()[-120:]}"
        time.sleep(4)
    return False, f"stack not healthy after 240s: {last}"


def psql(sql):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "srs", "-d", "srs", "-At", "-c", sql], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def post_roundtrip(_):
    run = lambda *a: subprocess.run(["docker", "compose", "run", "--rm", "migrate", "alembic", *a], cwd=ROOT, capture_output=True, text=True)
    d = run("downgrade", "base"); after_down = psql("select count(*) from information_schema.tables where table_schema='public' and table_name<>'alembic_version'")
    u = run("upgrade", "head"); after_up = psql("select string_agg(table_name, ',' order by table_name) from information_schema.tables where table_schema='public' and table_name<>'alembic_version'")
    ok = d.returncode == 0 and u.returncode == 0 and after_down == "0" and after_up == "audit_events,auth_sessions,jobs,projects,users"
    return ok, f"down_rc={d.returncode} tables_after_down={after_down} up_rc={u.returncode} tables_after_up={after_up}"


def post_drills(_):
    r = json.loads((ROOT / "validation-evidence" / "drills-evidence.json").read_text())["results"]
    return all(v == "PASS" for v in r.values()) and len(r) == 8, json.dumps(r)


def post_locks(_):
    need = [ROOT / "requirements.lock.txt", ROOT / "apps/web/package-lock.json", ROOT / "docs/runbooks/LOCK_ENVIRONMENT.txt"]
    miss = [str(p.relative_to(ROOT)) for p in need if not p.exists()]
    return not miss, "present" if not miss else f"MISSING: {miss} (run scripts/lock.sh with network, commit the results)"


def pre_env_file():
    ex = (ROOT / ".env.example").read_text()
    ex = re.sub(r"^POSTGRES_PASSWORD=.*$", f"POSTGRES_PASSWORD={PW}", ex, flags=re.M)
    ex = re.sub(r"^DATABASE_URL=.*$", f"DATABASE_URL=postgresql+psycopg://srs:{PW}@localhost:5432/srs", ex, flags=re.M)
    ex = re.sub(r"^RATE_LIMIT_REGISTER_PER_HOUR=.*$", "RATE_LIMIT_REGISTER_PER_HOUR=500", ex, flags=re.M)
    (ROOT / ".env").write_text(ex)  # .env is git-ignored


def env_step():
    out = {}
    for name, cmd in [("python", [sys.executable, "--version"]), ("node", ["node", "--version"]), ("npm", ["npm", "--version"]),
                      ("docker", ["docker", "--version"]), ("compose", ["docker", "compose", "version"]), ("git", ["git", "rev-parse", "HEAD"])]:
        try: out[name] = subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip() or "NOT INSTALLED"
        except Exception: out[name] = "NOT INSTALLED"
    (EV / "versions.json").write_text(json.dumps(out, indent=2))
    ok = out["python"].startswith("Python 3.12") and out["node"].startswith("v22") and "NOT" not in out["docker"] and "NOT" not in out["compose"]
    return ok, json.dumps(out)


bash = lambda s: ["bash", "-lc", s]
WEB = ROOT / "apps/web"
step("env", "Runtime versions (Python 3.12, Node 22, Docker+Compose)", post=lambda _: env_step())
step("locks", "Lockfiles committed (backend + frontend)", post=post_locks)
step("pip-install", "Install backend from lock into clean venv", requires=["env", "locks"],
     cmd=bash(f"rm -rf {VENV} && python3 -m venv {VENV} && {VENV}/bin/pip install -c requirements.lock.txt -e '.[dev]' && {VENV}/bin/pip check"))
step("npm-ci", "Install frontend with npm ci (lockfile-exact)", requires=["env", "locks"], cwd=WEB, cmd=bash("npm ci"))
step("ruff-check", "ruff check", requires=["pip-install"], cmd=bash(f"{VENV}/bin/ruff check services/api"))
step("ruff-format", "ruff format --check", requires=["pip-install"], cmd=bash(f"{VENV}/bin/ruff format --check services/api"))
step("mypy", "mypy --strict", requires=["pip-install"], cmd=bash(f"{VENV}/bin/mypy"))
step("stdlib", "stdlib-only tests (shim)", cmd=[sys.executable, "scripts/run_stdlib_tests.py"])
step("static", "static validation", cmd=[sys.executable, "scripts/validate_static.py"])
step("web-lint", "next lint", requires=["npm-ci"], cwd=WEB, cmd=bash("npm run lint"))
step("web-typecheck", "tsc --noEmit", requires=["npm-ci"], cwd=WEB, cmd=bash("npm run typecheck"))
step("web-unit", "vitest", requires=["npm-ci"], cwd=WEB, cmd=bash(f"npx vitest run --reporter=junit --outputFile={EV}/vitest.xml"), post=post_vitest)
step("web-build", "next build (production)", requires=["npm-ci"], cwd=WEB, env={"API_BASE_URL": "http://localhost:8000"}, cmd=bash("npm run build"))
step("stack-up", "Clean-environment start: down -v, up --build, migrations applied, health green", requires=["env", "locks"],
     pre=pre_env_file, cmd=bash("docker compose down -v --remove-orphans && docker compose up -d --build"), post=post_stack, timeout=1500)
step("migration-roundtrip", "Alembic downgrade base -> upgrade head on the live DB", requires=["stack-up"], cmd=None, post=post_roundtrip)
step("pytest", "pytest unit+integration vs REAL Postgres/Redis (REQUIRE_INTEGRATION=1, coverage)", requires=["stack-up", "pip-install"],
     env={"REQUIRE_INTEGRATION": "1", "TEST_DATABASE_URL": TEST_DB, "DATABASE_URL": TEST_DB, "REDIS_URL": "redis://localhost:6379/0", "APP_ENV": "test"},
     cmd=bash(f"docker compose exec -T postgres psql -U srs -d srs -c 'CREATE DATABASE srs_test' || true; "
              f"{VENV}/bin/pytest services/api/tests -p no:cacheprovider -rs --junitxml={EV}/pytest.xml --cov=app --cov-report=term --cov-report=xml:{EV}/coverage.xml"),
     post=post_pytest)
step("playwright", "Playwright E2E against the running stack", requires=["stack-up", "npm-ci"], cwd=WEB,
     env={"E2E_BASE_URL": "http://localhost:3000", "PLAYWRIGHT_JSON_OUTPUT_NAME": f"{EV}/playwright.json"},
     cmd=bash("npx playwright install --with-deps chromium && npx playwright test --reporter=json,list"), post=post_playwright)
step("cooldown", "Wait 65 s so the login rate-limit window resets before drills", requires=["stack-up"], cmd=bash("sleep 65"))
step("drills", "Live drills: ownership, CSRF, audit immutability, session revocation, IP-limit/isolation, broker outage, WORKER SIGKILL",
     requires=["stack-up", "cooldown"], cmd=[sys.executable, "scripts/drills.py", "run", "all"], post=post_drills, timeout=1500)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--only"); ap.add_argument("--dry-run", action="store_true"); a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    EV.mkdir(parents=True, exist_ok=True); results = {}
    for s in STEPS:
        sid = s["id"]
        if only and sid not in only: results[sid] = {"status": "NOT_RUN", "detail": "filtered out"}; continue
        if a.dry_run: results[sid] = {"status": "NOT_RUN", "detail": "dry-run", "cmd": s["cmd"] if s["cmd"] else "(python post-check)"}; print(f"PLAN  {sid}: {s['title']}"); continue
        bad = [r for r in s["requires"] if results.get(r, {}).get("status") not in ("PASS",) and not (only and r not in only and results.get(r, {}).get("status") == "NOT_RUN")]
        if bad: results[sid] = {"status": "BLOCKED", "detail": f"prerequisite not PASS: {bad}"}; print(f"BLOCK {sid}: {bad}"); continue
        t0 = time.time(); detail = ""; ok = True; rc = None
        try:
            if s["pre"]: s["pre"]()
            if s["cmd"]:
                with open(EV / f"{sid}.log", "w") as log:
                    log.write(f"$ {' '.join(map(str, s['cmd']))}\n(cwd={s['cwd']}, env+={list(s['env'])})\n\n"); log.flush()
                    p = subprocess.run(s["cmd"], cwd=s["cwd"], env={**os.environ, **s["env"]}, stdout=log, stderr=subprocess.STDOUT, timeout=s["timeout"])
                    rc = p.returncode; ok = rc == 0; detail = f"exit={rc}"
            if ok and s["post"]:
                ok, d = s["post"](None); detail = (detail + " | " if detail else "") + d
        except FileNotFoundError as e: ok, detail = False, f"tool missing: {e}"
        except subprocess.TimeoutExpired: ok, detail = False, f"timeout after {s['timeout']}s"
        except Exception as e: ok, detail = False, f"{type(e).__name__}: {e}"
        results[sid] = {"status": "PASS" if ok else "FAIL", "detail": detail, "seconds": round(time.time() - t0, 1), "exit": rc,
                        "cmd": " ".join(map(str, s["cmd"])) if s["cmd"] else "(python post-check)"}
        print(f"{results[sid]['status']:5s} {sid}: {detail[:160]}")
    (EV / "results.json").write_text(json.dumps(results, indent=2))
    lines = [f"# Validation run {STAMP}", "", "| step | status | detail |", "|---|---|---|"] + [f"| {k} | **{v['status']}** | {v['detail'][:200]} |" for k, v in results.items()]
    (EV / "REPORT.md").write_text("\n".join(lines) + "\n")
    sel = [v for v in results.values() if v["status"] != "NOT_RUN"]
    allpass = bool(sel) and all(v["status"] == "PASS" for v in sel) and not (only is None and not a.dry_run and any(v["status"] == "NOT_RUN" for v in results.values()))
    verdict = "DRY RUN (nothing executed)" if a.dry_run else ("ALL SELECTED STEPS PASSED" if allpass else "NOT PASSED")
    print(f"\nevidence: {EV}\nOVERALL: {verdict}")
    return 0 if allpass or a.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
