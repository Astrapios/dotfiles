"""Shared helpers for the mr-review GitLab scripts (run via glab, which carries auth).

Gotchas baked in:
  - `glab mr view --comments` omits anchors, resolved state and can drop comments; only the
    discussions API is authoritative, and it is paginated (per_page=100 is not "all").
  - `projects/:id` resolves from the git remote of the cwd; run from the worktree.
  - A draft-note PUT with only `note` wipes the position: always resend the full position.
  - `-f key[sub]=v` is sent as a flat JSON key and silently ignored by GitLab; nested payloads
    (position, line_range) must go as a JSON body via `--input` + Content-Type header.
"""
from __future__ import annotations

import json
import subprocess
import sys


def api(path: str, *args: str, method: str | None = None) -> object:
    """Call `glab api <path>` and parse JSON. Extra args go through verbatim (-f key=value ...)."""
    cmd = ["glab", "api"]
    if method:
        cmd += ["-X", method]
    cmd += [path, *args]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.stderr.write(out.stderr)
        raise SystemExit(f"glab api failed: {' '.join(cmd)}")
    return json.loads(out.stdout) if out.stdout.strip() else None


def paged(path: str) -> list:
    """Fetch every page of a list endpoint."""
    sep = "&" if "?" in path else "?"
    items: list = []
    page = 1
    while True:
        chunk = api(f"{path}{sep}per_page=100&page={page}")
        if not chunk:
            return items
        items.extend(chunk)
        if len(chunk) < 100:
            return items
        page += 1


def current_branch() -> str:
    return subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True).stdout.strip()


def resolve_mr(iid: str | None) -> dict:
    """MR JSON for an explicit IID, else the open MR whose source branch is the current branch."""
    if iid:
        return api(f"projects/:id/merge_requests/{iid}")
    branch = current_branch()
    mrs = api(f"projects/:id/merge_requests?source_branch={branch}&state=opened")
    if not mrs:
        raise SystemExit(f"no open MR for branch {branch}; pass the IID")
    return api(f"projects/:id/merge_requests/{mrs[0]['iid']}")
