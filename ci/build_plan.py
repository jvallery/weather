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

On a pull_request event the diff is HEAD^..HEAD of the checked-out merge commit,
which is the whole PR. $PINNED (space-separated names) lists images that build.yml
builds as their own jobs so pin-bump.yml can read their digests: for each one it
also writes <name>=true|false, and "unpinned" is the picked set without them.
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

def as_json(selected):
    return json.dumps([{k: r[k] for k in ("name", "dockerfile", "context")} for r in selected])


pinned = os.environ.get("PINNED", "").split()
unknown = [p for p in pinned if p not in {r["name"] for r in rows}]
if unknown:
    sys.exit(f"unknown PINNED image(s): {' '.join(unknown)}")
out = as_json(pick)
print(out)
if os.environ.get("GITHUB_OUTPUT"):
    picked = {r["name"] for r in pick}
    with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
        fh.write(f"images={out}\n")
        fh.write(f"unpinned={as_json([r for r in pick if r['name'] not in pinned])}\n")
        for name in pinned:
            fh.write(f"{name}={'true' if name in picked else 'false'}\n")
