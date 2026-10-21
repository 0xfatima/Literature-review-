#!/usr/bin/env python3
"""Minimal test runner for tests that have NO third-party imports (header marker: '# stdlib-only').

This exists because the authoring sandbox had no network, so pytest could not be installed.
It is NOT a substitute for pytest: no fixtures, no parametrize, no plugins. CI runs the same files
under real pytest. Output always says which runner produced it.
"""
import importlib, pathlib, sys, time, traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = ROOT / "services" / "api"
sys.path.insert(0, str(API))

def discover():
    for f in sorted((API / "tests").rglob("test_*.py")):
        if f.read_text().lstrip().startswith("# stdlib-only"):
            yield f

def main() -> int:
    passed = failed = 0
    failures = []
    for f in discover():
        mod_name = ".".join(f.relative_to(API).with_suffix("").parts)
        mod = importlib.import_module(mod_name)
        for name in sorted(n for n in dir(mod) if n.startswith("test_") and callable(getattr(mod, n))):
            t0 = time.perf_counter()
            try:
                getattr(mod, name)()
                passed += 1
                print(f"PASS  {mod_name}::{name}  ({(time.perf_counter()-t0)*1000:.1f} ms)")
            except Exception:
                failed += 1
                failures.append((f"{mod_name}::{name}", traceback.format_exc()))
                print(f"FAIL  {mod_name}::{name}")
    for n, tb in failures:
        print(f"\n--- {n}\n{tb}")
    print(f"\nRunner: stdlib shim (NOT pytest) | passed={passed} failed={failed}")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
