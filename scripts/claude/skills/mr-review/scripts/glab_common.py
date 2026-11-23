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
import os
import subprocess
import sys
from urllib.parse import quote

HOST = "gitlab.gc.arraylabs.net"
PROJECT = "product/site3d"


def _in_checkout() -> bool:
    probe = ["git", "rev-parse", "--is-inside-work-tree"]
    return subprocess.run(probe, check=False, capture_output=True, text=True).returncode == 0


def glab_argv(*args: str) -> tuple[list[str], dict[str, str]]:
    """The glab argv and child env for `glab <args>`, both naming the GitLab host.

    Args:
        *args: glab arguments, e.g. ("api", "-X", "PUT", "projects/:id/...", "--input", "body.json").

    Returns:
        The full argv and the environment to run it with: `GITLAB_HOST` set (the caller's value when it set
        one, else HOST), `--hostname` added to `glab api`, and outside a git checkout `:id` in a
        `projects/:id` endpoint replaced by the URL-encoded PROJECT.
    """
    env = dict(os.environ)
    host = env.get("GITLAB_HOST") or HOST
    env["GITLAB_HOST"] = host
    argv = ["glab", *args]
    if args and args[0] == "api":
        if any(a.startswith("projects/:id") for a in args) and not _in_checkout():
            argv = [a.replace("projects/:id", f"projects/{quote(PROJECT, safe='')}", 1)
                    if a.startswith("projects/:id") else a for a in argv]
        argv = ["glab", "api", "--hostname", host, *argv[2:]]
    return argv, env


def run_glab(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Run `glab <args>` against the GitLab host (see `glab_argv`), capturing text output.

    Args:
        *args: glab arguments.
        check: raise CalledProcessError on a nonzero exit.

    Returns:
        The completed process.
    """
    argv, env = glab_argv(*args)
    return subprocess.run(argv, capture_output=True, text=True, env=env, check=check)


def api(path: str, *args: str, method: str | None = None) -> object:
    """Call `glab api <path>` and parse JSON. Extra args go through verbatim (-f key=value ...)."""
    cmd = ["api"]
    if method:
        cmd += ["-X", method]
    cmd += [path, *args]
    out = run_glab(*cmd)
    if out.returncode != 0:
        sys.stderr.write(out.stderr)
        raise SystemExit(f"glab api failed: {' '.join(glab_argv(*cmd)[0])}")
    return json.loads(out.stdout) if out.stdout.strip() else None


def raw(path: str) -> str | None:
    """GET a non-JSON endpoint (a file's raw content); None when GitLab refuses it."""
    out = run_glab("api", path)
    return out.stdout if out.returncode == 0 else None


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
