#!/usr/bin/env python3
"""Static validation runnable WITHOUT third-party packages or network.

What it proves:  syntax of every Python/YAML/JSON/shell/JS/TS file; unused imports (approximate F401);
internal `app.*` imports resolve to real modules/names; .env.example <-> Settings consistency;
TypeScript *syntax* (not types) via a globally installed tsc if present.
What it does NOT prove: that anything runs. It is no substitute for ruff, mypy, pytest, tsc type-checking.
"""
import ast, json, pathlib, re, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = ROOT / "services" / "api"
fails: list[str] = []
def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    if not ok: fails.append(name)

py_files = sorted(p for p in API.rglob("*.py") if "__pycache__" not in p.parts) + sorted((ROOT / "scripts").glob("*.py"))

# 1. syntax
bad = []
trees = {}
for p in py_files:
    try: trees[p] = ast.parse(p.read_text(), str(p))
    except SyntaxError as e: bad.append(f"{p.relative_to(ROOT)}:{e.lineno} {e.msg}")
check(f"python syntax ({len(py_files)} files)", not bad, "; ".join(bad))

# 2. module/name index for internal import resolution
def modname(p):
    rel = p.relative_to(API).with_suffix("").parts
    return ".".join(rel[:-1] if rel[-1] == "__init__" else rel)
index = {}
for p, t in trees.items():
    if API / "app" in p.parents or p.parent == API / "app":
        names = set()
        for n in t.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): names.add(n.name)
            elif isinstance(n, ast.Assign):
                for tg in n.targets:
                    if isinstance(tg, ast.Name): names.add(tg.id)
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name): names.add(n.target.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names: names.add((a.asname or a.name).split(".")[0])
        index[modname(p)] = names
unresolved = []
for p, t in trees.items():
    for n in ast.walk(t):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.split(".")[0] in ("app", "tests") and n.level == 0:
            if n.module.startswith("tests"): continue
            if n.module not in index: unresolved.append(f"{p.relative_to(ROOT)}: module {n.module}"); continue
            for a in n.names:
                if a.name != "*" and a.name not in index[n.module] and f"{n.module}.{a.name}" not in index:
                    unresolved.append(f"{p.relative_to(ROOT)}: {n.module}.{a.name}")
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.startswith("app.") and a.name not in index: unresolved.append(f"{p.relative_to(ROOT)}: {a.name}")
check("internal app.* imports resolve", not unresolved, "; ".join(unresolved))

# 3. unused imports (approximate F401)
unused = []
for p, t in trees.items():
    if p.name == "__init__.py": continue
    imported = {}
    for n in ast.walk(t):
        if isinstance(n, ast.Import):
            for a in n.names: imported[(a.asname or a.name).split(".")[0]] = n.lineno
        elif isinstance(n, ast.ImportFrom) and n.module != "__future__":
            for a in n.names: imported[a.asname or a.name] = n.lineno
    src = p.read_text()
    used = {n.id for n in ast.walk(t) if isinstance(n, ast.Name)} | {n.value.id for n in ast.walk(t) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    for name, line in imported.items():
        if name not in used and not re.search(rf"[\"'\[ ]{re.escape(name)}[\"'\].| |]", src.split("\n", line)[-1]) and "noqa" not in src.splitlines()[line-1]:
            unused.append(f"{p.relative_to(ROOT)}:{line} {name}")
check("no unused imports (approx.)", not unused, "; ".join(unused))

# 4. .env.example <-> Settings
cfg = trees[API / "app/core/config.py"]
fields = {n.target.id.upper() for c in cfg.body if isinstance(c, ast.ClassDef) and c.name == "Settings"
          for n in c.body if isinstance(n, ast.AnnAssign)}
env_keys = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", (ROOT / ".env.example").read_text(), re.M))
allowed_extra = {"POSTGRES_PASSWORD", "NEXT_PUBLIC_APP_NAME", "NEXT_PUBLIC_SESSION_COOKIE_NAME"}
check("every Settings field is documented in .env.example", fields <= env_keys, ", ".join(sorted(fields - env_keys)))
check("every .env.example key is a Settings field or known extra", env_keys <= fields | allowed_extra,
      ", ".join(sorted(env_keys - fields - allowed_extra)))
check(".env.example has placeholders only", not re.search(r"(?i)(sk-|AIza|ghp_|AKIA)[A-Za-z0-9]{10,}", (ROOT / ".env.example").read_text()))

# 5. YAML / JSON
try:
    import yaml
    for f in [ROOT / "docker-compose.yml", ROOT / ".github/workflows/ci.yml"]:
        try:
            d = yaml.safe_load(f.read_text()); check(f"yaml parses: {f.relative_to(ROOT)}", isinstance(d, dict))
        except yaml.YAMLError as e:
            check(f"yaml parses: {f.relative_to(ROOT)}", False, str(e).splitlines()[-1][:120]); raise SystemExit("invalid YAML")
    comp = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    missing = [(s, d) for s, c in comp.items() for d in ((c.get("depends_on") or {}) if isinstance(c.get("depends_on"), dict) else {x: 1 for x in c.get("depends_on", [])}) if d not in comp]
    check("compose depends_on targets exist", not missing, str(missing))
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    check("CI has backend/web/e2e/docker-build jobs", {"backend", "web", "e2e", "docker-build"} <= set(ci["jobs"]))
except ImportError:
    check("pyyaml available", False)
for f in ["apps/web/package.json", "apps/web/tsconfig.json", "apps/web/.eslintrc.json", "apps/web/.prettierrc.json"]:
    try: json.loads((ROOT / f).read_text()); check(f"json parses: {f}", True)
    except Exception as e: check(f"json parses: {f}", False, str(e))
pkg = json.loads((ROOT / "apps/web/package.json").read_text())
check("package.json defines lint/typecheck/test:unit/build", {"lint", "typecheck", "test:unit", "build"} <= set(pkg["scripts"]))

# 6. shell + node
for f in sorted((ROOT / "scripts").glob("*.sh")):
    r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
    check(f"bash -n {f.name}", r.returncode == 0, r.stderr.strip())
for f in ["next.config.mjs", "postcss.config.mjs"]:
    r = subprocess.run(["node", "--check", str(ROOT / "apps/web" / f)], capture_output=True, text=True)
    check(f"node --check {f}", r.returncode == 0, r.stderr.strip()[:200])

# 7. TypeScript SYNTAX only (codes 1000-1999 are syntactic); type errors need installed deps and are not checked here
tsc = shutil.which("tsc")
if tsc:
    files = [str(p) for p in (ROOT / "apps/web").rglob("*.ts*") if "node_modules" not in p.parts and p.suffix in (".ts", ".tsx")]
    r = subprocess.run([tsc, "--noEmit", "--jsx", "preserve", "--skipLibCheck", "--noResolve", "--target", "es2022",
                        "--module", "esnext", "--moduleResolution", "bundler", *files], capture_output=True, text=True)
    syn = [l for l in r.stdout.splitlines() if re.search(r"error TS1\d{3}:", l)]
    check(f"TypeScript syntax ({len(files)} files; types NOT checked)", not syn, "; ".join(syn[:5]))
else:
    check("tsc available for TS syntax check", False, "NOT RUN")

print(f"\n{'ALL PASSED' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
