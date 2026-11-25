"""The review half of an MR's index.json (schema 2): which reviews were made, at which head, with which drafts.

The MR review app reads it to draw the Review tab, put each finding and draft on its line or thread, tell a draft's
state (not published, edited, published, rewritten, deleted) and say when the MR moved since the review. Called from
mr_context.py (`review add`, `check --review`); see references/thread-explanation.md "Reviews (schema 2)".

    "reviews": [{                          time order; the app shows the newest, earlier rounds folded
      "task": "T-uZ",                      astra task that made it
      "kind": "new|rereview|received-reply|self-review",
      "tier": "quick|standard|deep",
      "head_sha": "<40 hex>",              the MR head the review read
      "made_at": "<ISO 8601 with offset>",
      "state": "request-changes|ready|comments-only",
      "verdict": "<one sentence>",
      "page": "reviews/<head12>-<task>/index.html",   the briefing, copied in with the files it links
      "page_bytes": 1086549,
      "record": "docs/branches/<branch>-review.md",
      "file_sigs": {"<path>": "<sha1(diff)[:16]>"},   each changed file's diff at head_sha, as the app signs it
      "drafts": [{"id": 3939, "kind": "finding|reply|summary", "finding": "finding-1" | null,
                  "discussion_id": "<40 hex>" | null, "path": "<file>" | null, "line_from": 81 | null,
                  "line": 83 | null, "title": "<plain words>",
                  "posted_sha256": "<sha256 of the body GitLab returned on create>" | null,
                  "posted_text": "<the posted body, backfill only>"}]
    }]

A finding (the `findings` list) may also carry where it sits: severity, path, line_from, line.

A review page made from APP_LINKS_SINCE on links the MR in the MR review app beside each GitLab MR link, and each
listed draft at its spot there (app_link.py); `add_review` adds what the page lacks to its copy.

Standard library only.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app_link as AL  # noqa: E402

SCHEMA = 2
KINDS = ("new", "rereview", "received-reply", "self-review")
REVIEW_TIERS = ("quick", "standard", "deep")
STATES = ("request-changes", "ready", "comments-only")
DRAFT_KINDS = ("finding", "reply", "summary")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TASK = re.compile(r"^[A-Za-z0-9/_-]+$")
NONE = type(None)
REVIEW_KEYS = {"task": str, "kind": str, "tier": str, "head_sha": str, "made_at": str, "state": str, "verdict": str,
               "page": str, "page_bytes": int, "record": str, "file_sigs": dict, "drafts": list}
DRAFT_KEYS = {"id": int, "kind": str, "finding": (str, NONE), "discussion_id": (str, NONE), "path": (str, NONE),
              "line": (int, NONE), "title": str, "posted_sha256": (str, NONE)}
DRAFT_OPTIONAL = {"line_from": (int, NONE), "posted_text": str}
FINDING_LOCATION = {"severity": str, "path": (str, NONE), "line_from": (int, NONE), "line": (int, NONE)}
# (reviews made from here on must carry the MR review app's links; earlier pages predate app_link.py)
APP_LINKS_SINCE = dt.datetime.fromisoformat("2026-11-25T12:00:00-08:00")
LINKED = re.compile(r"""\b(?:src|href)\s*=\s*["']([^"'#?]+)""", re.IGNORECASE)


def save_atomic(path: Path, obj: dict) -> None:
    """Write JSON to `path` through a temp file in the same folder and os.replace: a reader never sees half a file.

    Args:
        path: The file to write.
        obj: What to write (indented JSON, one trailing newline).
    """
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(obj, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def diff_sig(diff: str) -> str:
    """A file's diff signed as the MR review app signs it (mrr/read.py `sig`)."""
    return hashlib.sha1(diff.encode()).hexdigest()[:16]


def sigs_of(changes: list[dict]) -> dict[str, str]:
    """{path: diff_sig} of GitLab's `changes` (or a version's `diffs`) list."""
    return {c.get("new_path") or c.get("old_path"): diff_sig(c.get("diff") or "") for c in changes}


def _types(tag: str, e: dict, keys: dict, out: list[str], required: bool = True) -> bool:
    ok = True
    for k, t in keys.items():
        if k not in e:
            if required:
                out.append(f"{tag}: no {k}")
                ok = False
        elif not isinstance(e[k], t) or (t is int and isinstance(e[k], bool)):
            out.append(f"{tag}: {k} has type {type(e[k]).__name__}")
            ok = False
    return ok


def links_due(made_at: str) -> bool:
    """Whether a page made at `made_at` must carry the app links (made from APP_LINKS_SINCE on)."""
    try:
        return dt.datetime.fromisoformat(made_at) >= APP_LINKS_SINCE
    except (TypeError, ValueError):
        return True


def _page_problems(tag: str, d: Path, r: dict, out: list[str], project: str | None = None,
                   iid: int | None = None) -> None:
    page = r["page"]
    if not page.startswith("reviews/") or ".." in Path(page).parts or Path(page).is_absolute():
        out.append(f"{tag}: page must be under reviews/ ({page})")
        return
    f = d / page
    if not f.is_file():
        out.append(f"{tag}: page {page} missing")
        return
    text = f.read_text(errors="replace")
    if "<script" in text.lower():
        out.append(f"{tag}: page has a script ({page})")
    if isinstance(iid, int) and links_due(r["made_at"]):
        drafts = [x for x in r["drafts"] if isinstance(x, dict) and isinstance(x.get("id"), int)]
        out += [f"{tag}: {x}" for x in AL.problems(text, drafts=drafts, iid=iid, project=project or AL.PROJECT)]


def review_problems(d: Path, r: dict, i: int, project: str | None = None, iid: int | None = None) -> list[str]:
    """Every way review `r` (the i-th) breaks the schema; its page must exist with no script, and (with the MR's
    `iid`, for a review made from APP_LINKS_SINCE on) carry its app links."""
    out: list[str] = []
    tag = f"review {i} ({r.get('task', '?')})"
    if not _types(tag, r, REVIEW_KEYS, out):
        return out
    if not TASK.match(r["task"]):
        out.append(f"{tag}: task {r['task']!r} is not a task id")
    if r["kind"] not in KINDS:
        out.append(f"{tag}: kind {r['kind']!r} not in {KINDS}")
    if r["tier"] not in REVIEW_TIERS:
        out.append(f"{tag}: tier {r['tier']!r} not in {REVIEW_TIERS}")
    if r["state"] not in STATES:
        out.append(f"{tag}: state {r['state']!r} not in {STATES}")
    if not HEX40.match(r["head_sha"]):
        out.append(f"{tag}: head_sha is not 40 hex")
    if not r["verdict"].strip():
        out.append(f"{tag}: no verdict")
    try:
        if dt.datetime.fromisoformat(r["made_at"]).utcoffset() is None:
            out.append(f"{tag}: made_at has no UTC offset")
    except ValueError:
        out.append(f"{tag}: made_at is not ISO 8601")
    for p, s in r["file_sigs"].items():
        if not (isinstance(s, str) and re.match(r"^[0-9a-f]{16}$", s)):
            out.append(f"{tag}: file_sigs[{p}] is not 16 hex")
    _page_problems(tag, d, r, out, project, iid)
    seen = set()
    for j, x in enumerate(r["drafts"]):
        dtag = f"{tag} draft {x.get('id', j) if isinstance(x, dict) else j}"
        if not isinstance(x, dict) or not _types(dtag, x, DRAFT_KEYS, out):
            continue
        _types(dtag, x, DRAFT_OPTIONAL, out, required=False)
        if x["id"] in seen:
            out.append(f"{dtag}: listed twice")
        seen.add(x["id"])
        if x["kind"] not in DRAFT_KINDS:
            out.append(f"{dtag}: kind {x['kind']!r} not in {DRAFT_KINDS}")
        if not x["title"].strip():
            out.append(f"{tag}: draft {x['id']} has no title")
        if x["posted_sha256"] is not None and not HEX64.match(x["posted_sha256"]):
            out.append(f"{dtag}: posted_sha256 is not 64 hex")
        if x["posted_sha256"] is None and "posted_text" not in x:
            out.append(f"{dtag}: needs posted_sha256 or posted_text")
        if x["discussion_id"] is not None and not HEX40.match(x["discussion_id"]):
            out.append(f"{dtag}: discussion_id is not 40 hex")
        if x["kind"] == "reply" and not x["discussion_id"]:
            out.append(f"{dtag}: a reply needs its discussion_id")
    return out


def problems(d: Path, idx: dict, task: str | None = None) -> list[str]:
    """Problems of the review half of `idx` (folder `d`): every review, and the findings' location keys.

    Args:
        d: The MR's mr_context folder.
        idx: Its index.json, parsed.
        task: When given, a review by this task must exist (`check --review`).

    Returns:
        One line per problem.
    """
    out: list[str] = []
    reviews = idx.get("reviews", [])
    if not isinstance(reviews, list):
        return ["reviews is not a list"]
    if reviews and idx.get("schema") != SCHEMA:
        out.append(f"reviews need schema {SCHEMA}, index says {idx.get('schema')!r}")
    for i, r in enumerate(reviews):
        out += (review_problems(d, r, i, idx.get("project"), idx.get("mr_iid")) if isinstance(r, dict)
                else [f"review {i}: not an object"])
    if task and not any(isinstance(r, dict) and r.get("task") == task for r in reviews):
        out.append(f"no review by {task} in index.json")
    for i, f in enumerate(idx.get("findings", []) if isinstance(idx.get("findings"), list) else []):
        if isinstance(f, dict):
            _types(f"finding {i} ({f.get('finding', '?')})", f, FINDING_LOCATION, out, required=False)
    return out


def _linked(html: str) -> list[str]:
    """The relative files a page links (src= and href=), never a URL, an absolute path, a data URI or a parent step."""
    out = []
    for m in LINKED.finditer(html):
        u = m.group(1).strip()
        if not u or re.match(r"^[a-z][a-z0-9+.-]*:", u, re.IGNORECASE) or u.startswith(("/", "//")) or ".." in u.split("/"):
            continue
        out.append(u)
    return out


def copy_page(page: Path, dest: Path) -> int:
    """Copy review page `page` to `dest`/index.html with the relative files it links; its size in bytes.

    The folder is emptied first, so a review written again leaves no stale file.
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    html = page.read_text()
    shutil.copyfile(page, dest / "index.html")
    for rel in _linked(html):
        src = page.parent / rel
        if src.is_file() and src.resolve() != page.resolve():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest / rel)
    return (dest / "index.html").stat().st_size


def add_review(
    d: Path,
    idx: dict,
    *,
    task: str,
    kind: str,
    tier: str,
    head: str,
    verdict: str,
    state: str,
    page: Path,
    record: str,
    drafts: list[dict],
    file_sigs: dict[str, str],
    made_at: str | None = None,
) -> dict:
    """Copy the page into reviews/<head12>-<task>/ and put the review into `idx` (saved by the caller).

    The copy gets the MR review app links the page lacks (app_link.stamp: beside each GitLab MR link, on the first
    "draft <id>" of each draft, and an "Open in MR app" line under the title when it has none).

    A review by the same task at the same head is replaced; the list stays in time order.

    Returns:
        The review entry written.
    """
    folder = f"reviews/{head[:12]}-{task.replace('/', '_')}"
    size = copy_page(Path(page), d / folder)
    if isinstance(idx.get("mr_iid"), int):   # (the copy gets the app links the page lacks)
        f = d / folder / "index.html"
        text = f.read_text()
        new = AL.stamp(text, drafts=drafts, iid=idx["mr_iid"], project=idx.get("project") or AL.PROJECT)
        if new != text:
            f.write_text(new)
            size = f.stat().st_size
    made = made_at or dt.datetime.now().astimezone().isoformat(timespec="seconds")
    entry = {"task": task, "kind": kind, "tier": tier, "head_sha": head, "made_at": made, "state": state,
             "verdict": verdict, "page": f"{folder}/index.html", "page_bytes": size, "record": record,
             "file_sigs": file_sigs, "drafts": drafts}
    idx["schema"] = max(int(idx.get("schema") or SCHEMA), SCHEMA)
    rs = [r for r in idx.get("reviews", []) if not (r.get("task") == task and r.get("head_sha") == head)]
    rs.append(entry)
    rs.sort(key=lambda r: dt.datetime.fromisoformat(r["made_at"]))
    idx["reviews"] = rs
    return entry


def drafts_from(path: Path) -> list[dict]:
    """The drafts list a review task saved (`draft_note.py --json` lines, plus kind, finding or thread, and title).

    Accepts a JSON list, or JSON lines; `reply_to` is read as discussion_id and `mr` is dropped.
    """
    text = Path(path).read_text().strip()
    items = json.loads(text) if text.startswith("[") else [json.loads(x) for x in text.splitlines() if x.strip()]
    out = []
    for x in items:
        y = {k: v for k, v in x.items() if k not in ("mr", "reply_to")}
        y.setdefault("discussion_id", x.get("reply_to"))
        y.setdefault("finding", None)
        y.setdefault("path", None)
        y.setdefault("line", None)
        y.setdefault("kind", "reply" if y["discussion_id"] else "finding")
        out.append(y)
    return out
