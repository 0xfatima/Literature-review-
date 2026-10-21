#!/usr/bin/env python3
"""Verify the Python and npm lockfiles are complete, exact, and consistent with the manifests.

    .venv-lock/bin/python scripts/verify_locks.py       # real check: run with the interpreter of the venv lock.sh built,
                                                        # so installed-package metadata is visible
    python3 scripts/verify_locks.py --selftest          # tests the verification LOGIC on synthetic data (no network/venv)

Python checks : every line is an exact `name==version` pin (no ranges, URLs, editables, duplicates); every direct
                dependency in pyproject.toml is pinned; the TRANSITIVE closure of those dependencies (walked through
                installed `Requires-Dist` metadata, markers evaluated) is fully pinned; pinned == installed versions.
npm checks    : lockfileVersion 2/3; root manifest in the lock equals package.json; every package entry has
                version + resolved + integrity (so `npm ci` is reproducible and tamper-evident).
"""
import json, pathlib, re, sys
from collections.abc import Callable
from importlib import metadata

try:
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
except ImportError:  # pip vendors packaging
    from pip._vendor.packaging.requirements import Requirement  # type: ignore
    from pip._vendor.packaging.utils import canonicalize_name  # type: ignore

ROOT = pathlib.Path(__file__).resolve().parent.parent
FREEZE_EXCLUDED = {"pip", "setuptools", "wheel", "distribute"}  # `pip freeze` never lists these
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;#]+)$")


def parse_lock(text: str) -> tuple[dict[str, str], list[str]]:
    pins: dict[str, str] = {}; problems: list[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"): continue
        m = PIN.match(line)
        if not m: problems.append(f"line {n}: not an exact pin: {line!r}"); continue
        name = canonicalize_name(m.group(1))
        if name in pins: problems.append(f"line {n}: duplicate pin for {name}")
        pins[name] = m.group(2)
    if not pins: problems.append("lock contains no pins")
    return pins, problems


def closure(roots: list[str], requires_of: Callable[[str], list[str] | None]) -> tuple[set[str], list[str]]:
    """Walk dependencies. requires_of(name) -> list of Requires-Dist strings, or None if the dist is not installed."""
    seen: set[str] = set(); missing: list[str] = []
    queue: list[tuple[str, frozenset[str]]] = []
    for r in roots:
        q = Requirement(r); queue.append((canonicalize_name(q.name), frozenset(q.extras)))
    done: set[tuple[str, frozenset[str]]] = set()
    while queue:
        name, extras = queue.pop()
        if (name, extras) in done: continue
        done.add((name, extras)); seen.add(name)
        reqs = requires_of(name)
        if reqs is None:
            missing.append(name); continue
        for s in reqs:
            r = Requirement(s)
            if r.marker is not None and not any(r.marker.evaluate({"extra": e}) for e in (extras or frozenset({""}))):
                continue
            queue.append((canonicalize_name(r.name), frozenset(r.extras)))
    return seen, missing


def check_python(lock_text: str, direct: list[str], requires_of: Callable[[str], list[str] | None],
                 installed_version: Callable[[str], str | None]) -> list[str]:
    pins, problems = parse_lock(lock_text)
    for d in direct:
        n = canonicalize_name(Requirement(d).name)
        if n not in pins: problems.append(f"direct dependency {n} is not pinned")
    reach, missing = closure(direct, requires_of)
    problems += [f"{m}: required but not installed in the verification venv" for m in missing]
    problems += [f"transitive dependency {n} is not pinned" for n in sorted(reach) if n not in pins and n not in missing and n not in FREEZE_EXCLUDED]
    for n, v in pins.items():
        iv = installed_version(n)
        if iv is not None and iv != v: problems.append(f"{n}: lock pins {v} but venv has {iv}")
    return problems


def check_npm(lock: dict, pkg: dict) -> list[str]:
    problems: list[str] = []
    if lock.get("lockfileVersion") not in (2, 3): problems.append(f"unexpected lockfileVersion {lock.get('lockfileVersion')}")
    packages = lock.get("packages") or {}
    root = packages.get("")
    if root is None: return problems + ["lock has no root package entry"]
    for sect in ("dependencies", "devDependencies"):
        if (root.get(sect) or {}) != (pkg.get(sect) or {}): problems.append(f"lock root {sect} differs from package.json (run npm install, commit the lock)")
    for key, e in packages.items():
        if key == "" or e.get("link"): continue
        for f in ("version", "resolved", "integrity"):
            if not e.get(f): problems.append(f"{key}: missing {f}")
    for name in {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}:
        if f"node_modules/{name}" not in packages: problems.append(f"direct dependency {name} not resolved in lock")
    return problems


def real_check() -> int:
    import tomllib
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    direct = list(proj["dependencies"]) + list(proj.get("optional-dependencies", {}).get("dev", []))
    def requires_of(name: str):
        try: return metadata.requires(name) or []
        except metadata.PackageNotFoundError: return None
    def version_of(name: str):
        try: return metadata.version(name)
        except metadata.PackageNotFoundError: return None
    lock = ROOT / "requirements.lock.txt"
    out = {}
    if not lock.exists(): out["python"] = ["requirements.lock.txt is missing"]
    else: out["python"] = check_python(lock.read_text(), direct, requires_of, version_of)
    wl = ROOT / "apps/web/package-lock.json"
    out["npm"] = check_npm(json.loads(wl.read_text()), json.loads((ROOT / "apps/web/package.json").read_text())) if wl.exists() else ["apps/web/package-lock.json is missing"]
    pins = parse_lock(lock.read_text())[0] if lock.exists() else {}
    npm_n = len([k for k in (json.loads(wl.read_text()).get("packages") or {}) if k]) if wl.exists() else 0
    for k, v in out.items():
        print(f"{'PASS' if not v else 'FAIL'}  {k} lock" + ("" if not v else ":\n  - " + "\n  - ".join(v[:25])))
    print(f"python pins: {len(pins)} (direct declared: {len(direct)}) | npm packages locked: {npm_n}")
    return 0 if not any(out.values()) else 1


def selftest() -> int:
    fails = 0
    def ok(name, cond):
        nonlocal fails; print(("PASS  " if cond else "FAIL  ") + name); fails += 0 if cond else 1
    graph = {"app-lib": ['web-fw>=1', 'orm>=2', "uvloop>=0.1; sys_platform == 'never-os'", 'extra-only>=1; extra == "speedups"'],
             "web-fw": ['http-core>=1', 'pyd>=2'], "http-core": [], "pyd": ['typing-ext>=4'], "typing-ext": [],
             "orm": ['typing-ext>=4', 'greenlet>=1; python_version >= "3"'], "greenlet": [], "pytest": ['pluggy>=1'], "pluggy": [],
             "extra-only": []}
    req = lambda n: graph.get(canonicalize_name(n))
    full = "\n".join(f"{n}==1.0" for n in ["app-lib", "web-fw", "http-core", "pyd", "typing-ext", "orm", "greenlet", "pytest", "pluggy"]) + "\n"
    inst = lambda n: "1.0" if canonicalize_name(n) in graph else None
    direct = ["app-lib>=1", "pytest>=8"]
    ok("complete transitive lock passes", check_python(full, direct, req, inst) == [])
    ok("marker-excluded dependency (uvloop on other OS) is not demanded", not any("uvloop" in p for p in check_python(full, direct, req, inst)))
    ok("extra-gated dependency not demanded when extra not requested", not any("extra-only" in p for p in check_python(full, direct, req, inst)))
    missing_trans = full.replace("typing-ext==1.0\n", "")
    p = check_python(missing_trans, direct, req, inst); ok("missing TRANSITIVE pin is detected (direct-only locks fail)", any("typing-ext" in x for x in p))
    direct_only = "app-lib==1.0\npytest==1.0\n"; ok("a 'direct dependencies only' lock fails", len(check_python(direct_only, direct, req, inst)) >= 5)
    ok("range specifier rejected", any("not an exact pin" in x for x in parse_lock("fastapi>=0.110\n")[1]))
    ok("URL/editable lines rejected", sum("not an exact pin" in x for x in parse_lock("-e .\nfoo @ file:///x\n")[1]) == 2)
    ok("duplicate pin rejected", any("duplicate" in x for x in parse_lock("a==1\nA==2\n")[1]))
    ok("version drift between lock and venv detected", any("pins 1.0 but venv has 2.0" in x for x in check_python(full, direct, req, lambda n: "2.0" if n == "pluggy" else "1.0")))
    g2 = {**graph, "pyd": ["typing-ext>=4", "setuptools>=60"], "setuptools": []}
    ok("deps on freeze-excluded packages (setuptools) are not false positives", check_python(full, direct, lambda n: g2.get(canonicalize_name(n)), lambda n: "1.0" if canonicalize_name(n) in g2 and n != "setuptools" else None) == [])
    ok("not-installed-but-required detected", any("not installed" in x for x in check_python(full, direct, lambda n: None if n == "pyd" else req(n), inst)))
    pkg = {"dependencies": {"next": "^15.0.0"}, "devDependencies": {"vitest": "^2.0.0"}}
    good = {"lockfileVersion": 3, "packages": {"": {"dependencies": {"next": "^15.0.0"}, "devDependencies": {"vitest": "^2.0.0"}},
            "node_modules/next": {"version": "15.0.1", "resolved": "https://r/next.tgz", "integrity": "sha512-x"},
            "node_modules/vitest": {"version": "2.0.1", "resolved": "https://r/v.tgz", "integrity": "sha512-y"}}}
    ok("consistent npm lock passes", check_npm(good, pkg) == [])
    bad = json.loads(json.dumps(good)); del bad["packages"]["node_modules/next"]["integrity"]
    ok("npm entry without integrity detected", any("missing integrity" in x for x in check_npm(bad, pkg)))
    stale = json.loads(json.dumps(good)); stale["packages"][""]["dependencies"]["next"] = "^14.0.0"
    ok("npm lock stale vs package.json detected", any("differs from package.json" in x for x in check_npm(stale, pkg)))
    ok("lockfileVersion 1 rejected", any("lockfileVersion" in x for x in check_npm({**good, "lockfileVersion": 1}, pkg)))
    print(f"\nselftest: {'ALL PASSED' if not fails else str(fails) + ' FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else real_check())
