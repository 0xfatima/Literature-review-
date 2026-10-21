#!/usr/bin/env python3
"""List every GitHub Action used in .github/workflows with: pinned ref, latest release, and the runtime
(`runs.using`, e.g. node20/node24) declared by the pinned ref and by the latest release.

    python scripts/check_actions.py            # needs network (api.github.com, raw.githubusercontent.com); GITHUB_TOKEN optional
    python scripts/check_actions.py --offline  # just parse and list what would be checked

It deliberately makes NO recommendation about which runtime is deprecated: read the output together with the
warnings in the CI log and the actions' release notes, then bump deliberately.
"""
import json, os, pathlib, re, sys, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
USES = re.compile(r"^\s*-?\s*uses:\s*([\w.-]+/[\w.-]+)((?:/[\w./-]+)?)@([\w.-]+)\s*(?:#.*)?$", re.M)


def discover() -> list[tuple[str, str, str, str]]:
    found = {}
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        for repo, sub, ref in USES.findall(wf.read_text()):
            found[(repo, sub, ref)] = found.get((repo, sub, ref), []) + [wf.name]
    return [(r, s, ref, ", ".join(sorted(set(w)))) for (r, s, ref), w in sorted(found.items())]


def get(url: str):
    h = {"User-Agent": "srs-check-actions", "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN") and "api.github.com" in url: h["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as r: return r.read().decode()


def runtime(repo: str, sub: str, ref: str) -> str:
    for name in ("action.yml", "action.yaml"):
        try:
            m = re.search(r"^\s*using:\s*['\"]?([\w.-]+)", get(f"https://raw.githubusercontent.com/{repo}/{ref}{sub}/{name}"), re.M)
            return m.group(1) if m else "?"
        except urllib.error.HTTPError: continue
        except Exception as e: return f"error: {e}"
    return "action.yml not found"


def latest(repo: str) -> str:
    try: return json.loads(get(f"https://api.github.com/repos/{repo}/releases/latest"))["tag_name"]
    except Exception as e: return f"error: {e}"


def major(tag: str) -> int | None:
    m = re.match(r"v?(\d+)", tag); return int(m.group(1)) if m else None


def main() -> int:
    items = discover()
    if "--offline" in sys.argv:
        for r, s, ref, w in items: print(f"{r}{s}@{ref}  ({w})")
        print(f"\n{len(items)} distinct action references found")
        return 0 if items else 1
    print("| action | used in | pinned | pinned runtime | latest release | latest runtime | note |\n|---|---|---|---|---|---|---|")
    for repo, sub, ref, wf in items:
        lt = latest(repo); pr = runtime(repo, sub, ref); lr = runtime(repo, sub, lt) if not lt.startswith("error") else "?"
        note = ""
        if major(lt) and major(ref) and major(lt) > major(ref): note += "newer major available; read release notes. "
        if pr != lr and lr != "?": note += f"runtime differs ({pr} -> {lr}). "
        print(f"| {repo}{sub} | {wf} | {ref} | {pr} | {lt} | {lr} | {note.strip()} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
