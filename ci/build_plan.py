#!/usr/bin/env python3
"""Pick the images to build from images.tsv (next to this script) (stdlib only; any forge, any runner).

    python3 build_plan.py            images whose paths changed in the pushed range
                                        (event "before"..HEAD; HEAD^..HEAD when unknown)
    python3 build_plan.py all        every image
    python3 build_plan.py NAME ...   the named images (unknown names fail)
    With no arguments, $IMAGES (if set) is read as the argument list.

images.tsv columns (tab-separated): name, dockerfile, context, paths. Paths are
space-separated path prefixes (a trailing "/" means a directory), the same
build_paths native CI used. Prints a JSON array of {name, dockerfile, context}
and, under a forge, writes images=<json> to $GITHUB_OUTPUT.
"""
import json, os, subprocess, sys

rows = []
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "images.tsv")) as fh:
    for line in fh:
        if not line.strip() or line.startswith("#"):
            continue
        name, dockerfile, context, paths = line.rstrip("\n").split("\t")
        rows.append({"name": name, "dockerfile": dockerfile, "context": context, "paths": paths.split()})

want = (" ".join(sys.argv[1:]) or os.environ.get("IMAGES", "")).split()
if want:
    names = {r["name"] for r in rows}
    unknown = [w for w in want if w != "all" and w not in names]
    if unknown:
        sys.exit(f"unknown image(s): {' '.join(unknown)}")
    pick = [r for r in rows if "all" in want or r["name"] in want]
else:
    def known(rev):
        return bool(rev) and subprocess.run(["git", "cat-file", "-e", rev + "^{commit}"], capture_output=True).returncode == 0
    # A push can carry several commits (rebase merges): diff the whole pushed range.
    # The push event's "before" SHA, read from the event file (GitHub and Forgejo
    # both set GITHUB_EVENT_PATH); $BEFORE overrides it for local runs.
    before = os.environ.get("BEFORE", "").strip()
    if not before and os.environ.get("GITHUB_EVENT_PATH"):
        try:
            with open(os.environ["GITHUB_EVENT_PATH"]) as fh:
                before = str(json.load(fh).get("before") or "")
        except (OSError, ValueError):
            before = ""
    base = before if before.strip("0") and known(before) else ("HEAD^" if known("HEAD^") else "")
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", base, "HEAD"] if base else ["git", "ls-files"], text=True
    ).split()
    pick = [r for r in rows if any(c == p or c.startswith(p if p.endswith("/") else p + "/") or c == p.rstrip("/")
                                   for c in changed for p in r["paths"])]

out = json.dumps([{k: r[k] for k in ("name", "dockerfile", "context")} for r in pick])
print(out)
if os.environ.get("GITHUB_OUTPUT"):
    with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
        fh.write(f"images={out}\n")
