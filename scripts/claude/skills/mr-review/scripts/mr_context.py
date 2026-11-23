#!/usr/bin/env python3
"""Keep the per-MR thread explanations folder and its index.json (see references/thread-explanation.md).

Layout: site3d_project_root/outputs/mr_context/<iid>/
    <discussion_id>.html   one explanation block per thread (self-contained, dark, figures as data URIs)
    finding-<n>.html       one block per review finding whose draft has no thread yet
    deep_<topic>.html      Deep pages, shared by the threads on one topic
    index.json             one entry per thread (and per finding), the schema below; the MR review app reads it

Usage:
    mr_context.py add DIR (--discussion ID | --finding finding-N) --head SHA --tier short|standard|deep
                  --title TEXT --last-note ID --anchor-status as-posted|inferred|general
                  [--evidence IID=SHA ...] [--deep deep_x.html] [--cold-read pass|rewritten|gap]
                  [--cold-read-note TEXT] [--requested-by button|review|watcher] [--draft-note N]
                  [--project product/site3d] [--iid N]
    mr_context.py check DIR [--head SHA] [--threads THREADS.json] [--mr-head IID=SHA ...]
                  exit 1 unless every entry is valid, its files exist, its draft has no unfilled slot,
                  every promise in it is queued, and (with --threads) no thread has a newer note
    mr_context.py check DIR --review TASK   also: a review by TASK is listed, its page is there with no script,
                  every draft titled (mr_review_index.py)
    mr_context.py review add DIR --task T-x --kind new|rereview|received-reply|self-review --tier quick|standard|deep
                  --head SHA --state request-changes|ready|comments-only --verdict TEXT --page PAGE.html
                  --record RECORD.md --drafts DRAFTS.json [--changes CHANGES.json | --no-sigs] [--made-at ISO]
                  copy the review page into DIR/reviews/<head12>-<task>/ and list the review
    mr_context.py overview add DIR --head SHA --task T-x --cold-read pass|rewritten|gap|pending
                  [--cold-read-note TEXT] [--requested-by review|button] [--made-at ISO]
                  list the overview mr_overview.py built in DIR at SHA (refused when its check fails)
    mr_context.py check DIR --overview SHA   also: the overview entry is at SHA, its page, data and drawings are
                  there with no script, no handler and no outside link, every thread anchors to a listed box
    mr_context.py promises DIR [--md]   the queued fixes the drafts promise ("I'll ...")
    mr_context.py text DIR/<discussion_id>.html   the block's words, figures listed by caption (cold read)

Schema 2 adds last_note_id, anchor_status, evidence and promises to an entry, and the review half
(mr_review_index.py): `reviews` (one per review round: its page, verdict and drafts) and each finding's
severity, path and lines. Schema 1 entries (written before Nov 20 2026) still pass; their missing keys
give a warning.

Standard library only, so any Python runs it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mr_overview as MO  # noqa: E402
import mr_review_index as RI  # noqa: E402

SCHEMA = 2
SCHEMAS = (1, 2)
TIERS = ("short", "standard", "deep")
VERDICTS = ("pass", "rewritten", "gap", "pending")
REQUESTERS = ("button", "review", "watcher")
ANCHOR_STATUSES = ("as-posted", "inferred", "general")
PROMISE_STATUSES = ("queued", "done", "dropped")
ENTRY_KEYS = {
    "discussion_id": str,  # GitLab discussion id (40 hex)
    "head_sha": str,  # MR head the explanation was written at (40 hex)
    "tier": str,  # short | standard | deep
    "title": str,  # one line: what the thread is about, plain words
    "block": str,  # file name of the block, <discussion_id>.html
    "deep_page": (str, type(None)),  # file name of the Deep page, or null
    "cold_read": str,  # pass | rewritten | gap | pending
    "cold_read_note": str,  # what the cold reader missed, empty on pass
    "requested_by": str,  # button | review | watcher
    "made_at": str,  # ISO 8601 with UTC offset, e.g. 2026-11-19T23:40:00-08:00
}
V2_KEYS = {
    "last_note_id": int,  # id of the thread's last note when written; a newer note makes the entry stale
    "anchor_status": str,  # as-posted | inferred (GitLab moved the position) | general (no position)
    "evidence": list,  # [{"mr": iid, "sha": 40 hex}]: every revision the block's facts come from
    "promises": list,  # [{"text": "I'll ...", "status": "queued|done|dropped"}]: one per promise in the draft
}
# A finding the review raised whose draft has no thread yet (the !808 layout): no discussion id,
# no thread to be fresh against.
FINDING_KEYS = {"finding": str, **{k: v for k, v in ENTRY_KEYS.items() if k != "discussion_id"}}
FINDING_OPTIONAL = {
    "draft_note_id": (int, type(None)),
    "evidence": list,
    "promises": list,
    # where the finding sits (severity, path, line_from, line), drawn on its lines by the app; typed by
    # mr_review_index.problems
    **{k: object for k in RI.FINDING_LOCATION},
}
# reviews: mr_review_index.py; overview: the MR architecture overview (references/mr-overview.md)
INDEX_KEYS = {"schema", "project", "mr_iid", "entries", "findings", "reviews", "overview"}
OVERVIEW_REQUESTERS = ("review", "button")
OVERVIEW_KEYS = {
    "head_sha": str,  # MR head the overview was built at (40 hex)
    "base_sha": str,  # merge base of the MR at that head (40 hex)
    "made_at": str,  # ISO 8601 with UTC offset
    "task": str,  # the astra task that built it
    "requested_by": str,  # review | button
    "page": str,  # overview-<head12>.html
    "data": str,  # overview-<head12>.json (facts, notes, boxes, anchors)
    "svgs": dict,  # {"map": ..., "flow": ..., "pipe": ...}
    "cold_read": str,  # pass | rewritten | gap | pending
    "cold_read_note": str,  # what the cold reader missed, empty on pass
    "boxes": list,  # [{"id", "kind", "status", "line"}]
    "threads": dict,  # discussion id -> box id or null
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
FINDING_NAME = re.compile(r"^finding-\d+$")

# --- drafts: where they are, slots, promises ---

# An unfilled slot: square brackets holding a letter, not an index (a[0]), a link ([x](url), [x][1],
# [x]: url), a checkbox ([ ], [x]) or a number list ([1, 2]). Code is masked before this runs.
SLOT = re.compile(r"(?<![\w\])\]])\[([^\[\]\n]*[A-Za-z][^\[\]\n]*)\](?![(\[:])")
JISU_SLOT = re.compile(r"^\s*JISU\s*:")
PROMISE = re.compile(r"\b(?:I|[Ww]e)(?:['’]ll| will)\b")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=\S)|\n\s*\n")
FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
SPAN = re.compile(r"`[^`\n]*`")


def mask_code(text: str) -> str:
    """Blank out code, keeping every offset: fences become newlines, backtick spans become 'x'.

    Args:
        text: Draft text with code in backticks or fences.

    Returns:
        A string of the same length, safe to search for slots and sentence ends.
    """
    out = FENCE.sub(lambda m: "\n" * len(m.group(0)), text)
    return SPAN.sub(lambda m: "x" * len(m.group(0)), out)


def find_slots(masked: str) -> list[str]:
    """Every unfilled `[...]` slot in already-masked draft text."""
    out = []
    for m in SLOT.finditer(masked):
        inner = m.group(1).strip()
        if inner.lower() == "x":
            continue
        out.append(m.group(0))
    return out


def find_promises(text: str) -> list[str]:
    """Each sentence of a draft that promises a change ("I'll ...", "I will ...", "we'll ...").

    Sentences end at '.', '!' or '?' followed by space, at a blank line and at a code fence;
    dots inside code do not end one.
    """
    masked = mask_code(text)
    out = []
    start = 0
    bounds = [m.end() for m in SENTENCE_END.finditer(masked)] + [len(text)]
    for end in bounds:
        seg_masked = masked[start:end]
        if PROMISE.search(seg_masked):
            seg = text[start:end]
            # drop any fence that the masked copy turned into newlines
            cut = seg_masked.find("\n\n\n")
            if cut > 0:
                seg = seg[:cut]
            out.append(re.sub(r"\s+", " ", seg).strip())
        start = end
    return out


class _Draft(HTMLParser):
    """Collect the text of each draft element: class "draft" or "reply" on div, pre, blockquote, p, section."""

    TAGS = {"div", "pre", "blockquote", "p", "section"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.cur: list[str] = []
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        cls = (dict(attrs).get("class") or "").split()
        if self.depth:
            self.depth += 1 if tag not in ("br", "img", "hr") else 0
            if tag == "code":
                self.cur.append("`")
            if tag in ("p", "li", "br", "div"):
                self.cur.append("\n")
        elif tag in self.TAGS and ({"draft", "reply"} & set(cls)):
            self.depth = 1
            self.cur = []

    def handle_endtag(self, tag):
        if not self.depth or tag in ("br", "img", "hr"):
            return
        if tag == "code":
            self.cur.append("`")
        self.depth -= 1
        if self.depth == 0:
            self.out.append("".join(self.cur).strip())

    def handle_data(self, data):
        if self.depth:
            self.cur.append(data)


def extract_drafts(page: str) -> list[str]:
    """The text of every draft reply in a block (code in backticks)."""
    p = _Draft()
    p.feed(page)
    return p.out


# --- index ---


def _load(d: Path) -> dict:
    p = d / "index.json"
    if not p.exists():
        return {"schema": SCHEMA, "project": None, "mr_iid": None, "entries": []}
    return json.loads(p.read_text())


def _save(d: Path, idx: dict) -> None:
    idx["entries"].sort(key=lambda e: e["made_at"])
    RI.save_atomic(d / "index.json", idx)  # (temp file and os.replace: the app never reads half a file)


def _check_types(tag: str, e: dict, keys: dict, out: list[str]) -> bool:
    ok = True
    for k, t in keys.items():
        if k not in e:
            out.append(f"{tag}: no {k}")
            ok = False
        elif not isinstance(e[k], t) or (t is int and isinstance(e[k], bool)):
            out.append(f"{tag}: {k} has type {type(e[k]).__name__}")
            ok = False
    return ok


def _check_common(tag: str, e: dict, d: Path, head: str | None, out: list[str]) -> None:
    if not HEX40.match(e["head_sha"]):
        out.append(f"{tag}: head_sha is not 40 hex")
    elif head and e["head_sha"] != head:
        out.append(f"{tag}: written at {e['head_sha'][:9]}, MR head is {head[:9]}")
    if e["tier"] not in TIERS:
        out.append(f"{tag}: tier {e['tier']!r} not in {TIERS}")
    if e["cold_read"] not in VERDICTS:
        out.append(f"{tag}: cold_read {e['cold_read']!r} not in {VERDICTS}")
    if e["cold_read"] in ("rewritten", "gap") and not e["cold_read_note"]:
        out.append(f"{tag}: cold_read {e['cold_read']} needs a cold_read_note")
    if e["requested_by"] not in REQUESTERS:
        out.append(f"{tag}: requested_by {e['requested_by']!r} not in {REQUESTERS}")
    if e["tier"] == "deep" and not e["deep_page"]:
        out.append(f"{tag}: a deep entry needs deep_page")
    if e["deep_page"] and not (d / e["deep_page"]).exists():
        out.append(f"{tag}: deep page {e['deep_page']} missing")
    try:
        if dt.datetime.fromisoformat(e["made_at"]).utcoffset() is None:
            out.append(f"{tag}: made_at has no UTC offset")
    except ValueError:
        out.append(f"{tag}: made_at is not ISO 8601")


def _check_evidence(tag: str, e: dict, mr_heads: dict[int, str], out: list[str]) -> None:
    for i, ev in enumerate(e["evidence"]):
        if not (isinstance(ev, dict) and isinstance(ev.get("mr"), int) and HEX40.match(str(ev.get("sha")))):
            out.append(f"{tag}: evidence item {i} must be {{'mr': iid, 'sha': 40 hex}}")
            continue
        now = mr_heads.get(ev["mr"])
        if now and now != ev["sha"]:
            out.append(f"{tag}: evidence from !{ev['mr']} at {ev['sha'][:9]}, its head is now {now[:9]}")


def _check_block(tag: str, e: dict, d: Path, out: list[str], warn: list[str]) -> None:
    blk = d / e["block"]
    if not blk.exists():
        out.append(f"{tag}: block file {e['block']} missing")
        return
    txt = blk.read_text()
    if "<script" in txt.lower():
        out.append(f"{tag}: block has a script")
    if re.search(r'<img[^>]+src="(?!data:)', txt):
        out.append(f"{tag}: block has an image that is not a data URI")
    if "<img" not in txt and "<svg" not in txt:
        out.append(f"{tag}: block has no figure")
    drafts = extract_drafts(txt)
    if not drafts:
        warn.append(f"{tag}: no draft element (class draft or reply); slots and promises not checked")
        return
    said: list[str] = []
    for draft in drafts:
        for s in find_slots(mask_code(draft)):
            if JISU_SLOT.match(s[1:-1]):
                warn.append(f"{tag}: draft keeps a slot for Jisu {s}")
            else:
                out.append(f"{tag}: draft has an unfilled slot {s}")
        said += find_promises(draft)
    if "promises" not in e:
        for p in said:
            warn.append(f"{tag}: promise (no promises list, schema 1): {p}")
        return
    listed = {}
    for i, p in enumerate(e["promises"]):
        if not (isinstance(p, dict) and isinstance(p.get("text"), str)):
            out.append(f"{tag}: promises item {i} must be {{'text': ..., 'status': ...}}")
            continue
        if p.get("status") not in PROMISE_STATUSES:
            out.append(f"{tag}: promise status {p.get('status')!r} not in {PROMISE_STATUSES}")
        listed[p["text"]] = p
    for p in said:
        if p not in listed:
            out.append(f"{tag}: promise not queued (rerun add): {p}")


def audit(
    d: Path,
    head: str | None = None,
    threads: list[dict] | None = None,
    mr_heads: dict[int, str] | None = None,
    review: str | None = None,
    overview: str | None = None,
) -> tuple[list[str], list[str]]:
    """Every way the folder breaks the schema or the draft rules, and what only deserves a warning.

    Args:
        d: The MR's mr_context folder.
        head: When given, every entry must be at this head SHA.
        threads: `mr_discussions.py --json --all` output; an entry whose thread has a note newer
            than its last_note_id is stale even at the same head.
        mr_heads: Current head per MR iid; evidence taken from that MR at another SHA is stale.
        review: When given, a review by this task must be listed (mr_review_index.problems).
        overview: When given, an overview at this head must be listed, its files there and safe.

    Returns:
        (problems, warnings), one line each; the folder passes when problems is empty.
    """
    out: list[str] = []
    warn: list[str] = []
    mr_heads = mr_heads or {}
    p = d / "index.json"
    if not p.exists():
        return [f"{p}: missing"], warn
    try:
        idx = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return [f"{p}: not JSON ({e})"], warn
    if idx.get("schema") not in SCHEMAS:
        out.append(f"schema is {idx.get('schema')!r}, expected one of {SCHEMAS}")
    if not isinstance(idx.get("mr_iid"), int):
        out.append("mr_iid missing or not an integer")
    if not isinstance(idx.get("project"), str):
        out.append("project missing")
    extra = set(idx) - INDEX_KEYS
    if extra:
        out.append(f"unknown index keys {sorted(extra)}")
    out += RI.problems(d, idx, review)
    if "overview" in idx:
        out += overview_problems(d, idx["overview"], overview)
    elif overview:
        out.append(f"no overview in index.json (mr_overview.py make, then mr_context.py overview add, at {overview[:9]})")
    entries = idx.get("entries")
    findings = idx.get("findings", [])
    if not isinstance(entries, list) or not isinstance(findings, list):
        return out + ["entries missing or not a list"], warn
    if not (entries or findings or idx.get("reviews") or idx.get("overview")):
        return out + ["entries missing or empty"], warn
    by_id = {t["id"]: t for t in threads or []}
    seen = set()
    for i, e in enumerate(entries):
        tag = f"entry {i} ({str(e.get('discussion_id', '?'))[:8]})"
        if not _check_types(tag, e, ENTRY_KEYS, out):
            continue
        v2 = [k for k in V2_KEYS if k in e]
        if v2 and not _check_types(tag, e, V2_KEYS, out):
            continue
        unknown = set(e) - set(ENTRY_KEYS) - set(V2_KEYS)
        if unknown:
            out.append(f"{tag}: unknown keys {sorted(unknown)}")
        if not HEX40.match(e["discussion_id"]):
            out.append(f"{tag}: discussion_id is not 40 hex")
        if e["discussion_id"] in seen:
            out.append(f"{tag}: duplicate discussion_id")
        seen.add(e["discussion_id"])
        if e["block"] != f"{e['discussion_id']}.html":
            out.append(f"{tag}: block must be <discussion_id>.html")
        _check_common(tag, e, d, head, out)
        if v2:
            if e["anchor_status"] not in ANCHOR_STATUSES:
                out.append(f"{tag}: anchor_status {e['anchor_status']!r} not in {ANCHOR_STATUSES}")
            _check_evidence(tag, e, mr_heads, out)
        else:
            warn.append(f"{tag}: written before schema 2: fresh by head only, no last_note_id")
        if threads is not None:
            t = by_id.get(e["discussion_id"])
            if t is None:
                warn.append(f"{tag}: not in the thread list (deleted, or listed without --all)")
            elif v2:
                newer = [n["id"] for n in t.get("notes", []) if n["id"] > e["last_note_id"]]
                if newer:
                    word = "note" if len(newer) == 1 else "notes"
                    ids = ", ".join(str(n) for n in newer)
                    out.append(f"{tag}: stale: {len(newer)} newer {word} ({ids}) since {e['last_note_id']}")
        if e["block"].endswith(".html"):
            _check_block(tag, e, d, out, warn)
    for i, f in enumerate(findings):
        tag = f"finding {i} ({f.get('finding', '?')})"
        if not _check_types(tag, f, FINDING_KEYS, out):
            continue
        for k, t in FINDING_OPTIONAL.items():
            if k in f and not isinstance(f[k], t):
                out.append(f"{tag}: {k} has type {type(f[k]).__name__}")
        unknown = set(f) - set(FINDING_KEYS) - set(FINDING_OPTIONAL)
        if unknown:
            out.append(f"{tag}: unknown keys {sorted(unknown)}")
        if not FINDING_NAME.match(f["finding"]):
            out.append(f"{tag}: finding name must be finding-<n>")
        if f["block"] != f"{f['finding']}.html":
            out.append(f"{tag}: block must be <finding>.html")
        _check_common(tag, f, d, head, out)
        if isinstance(f.get("evidence"), list):
            _check_evidence(tag, f, mr_heads, out)
        _check_block(tag, f, d, out, warn)
    return out, warn


def overview_problems(d: Path, ov: object, head: str | None = None) -> list[str]:
    """Problems of the `overview` entry: its keys, its head, its files (mr_overview.check_dir), its anchors.

    Args:
        d: The MR's mr_context folder.
        ov: index.json's `overview` value.
        head: When given, the overview must be at this head SHA.

    Returns:
        One line per problem, each starting with "overview".
    """
    if not isinstance(ov, dict):
        return ["overview: not an object"]
    out: list[str] = []
    if not _check_types("overview", ov, OVERVIEW_KEYS, out):
        return out
    unknown = set(ov) - set(OVERVIEW_KEYS)
    if unknown:
        out.append(f"overview: unknown keys {sorted(unknown)}")
    for k in ("head_sha", "base_sha"):
        if not HEX40.match(ov[k]):
            out.append(f"overview: {k} is not 40 hex")
    if out:
        return out
    if head and ov["head_sha"] != head:
        out.append(f"overview: written at {ov['head_sha'][:9]}, MR head is {head[:9]}")
    if ov["requested_by"] not in OVERVIEW_REQUESTERS:
        out.append(f"overview: requested_by {ov['requested_by']!r} not in {OVERVIEW_REQUESTERS}")
    if ov["cold_read"] not in VERDICTS:
        out.append(f"overview: cold_read {ov['cold_read']!r} not in {VERDICTS}")
    if ov["cold_read"] in ("rewritten", "gap") and not ov["cold_read_note"]:
        out.append(f"overview: cold_read {ov['cold_read']} needs a cold_read_note")
    try:
        if dt.datetime.fromisoformat(ov["made_at"]).utcoffset() is None:
            out.append("overview: made_at has no UTC offset")
    except ValueError:
        out.append("overview: made_at is not ISO 8601")
    n = MO.names(ov["head_sha"])
    want = {"page": n["page"], "data": n["data"]}
    for k, v in want.items():
        if ov[k] != v:
            out.append(f"overview: {k} is {ov[k]!r}, expected {v!r}")
    if ov["svgs"] != {"map": n["map"], "flow": n["flow"], "pipe": n["pipe"]}:
        out.append(f"overview: svgs must name {n['map']}, {n['flow']}, {n['pipe']}")
    listed = {b.get("id") for b in ov["boxes"] if isinstance(b, dict)}
    for i, b in enumerate(ov["boxes"]):
        if not (isinstance(b, dict) and {"id", "kind", "status", "line"} <= set(b)):
            out.append(f"overview: box {i} must have id, kind, status, line")
    for disc, bid in ov["threads"].items():
        if bid is not None and bid not in listed:
            out.append(f"overview: thread {disc[:8]} anchored to {bid}, which is not a listed box")
    probs, _ = MO.check_dir(d, ov["head_sha"])
    out += [f"overview: {p}" for p in probs]
    return out


def problems(d: Path, head: str | None = None, review: str | None = None) -> list[str]:
    """Every way the folder breaks the schema; empty when it is valid (see `audit`)."""
    return audit(d, head, review=review)[0]


def _parse_mr_sha(items: list[str] | None) -> dict[int, str]:
    out = {}
    for it in items or []:
        iid, _, sha = it.partition("=")
        if not iid.lstrip("!").isdigit() or not HEX40.match(sha):
            raise SystemExit(f"expected IID=<40 hex>, got {it!r}")
        out[int(iid.lstrip("!"))] = sha
    return out


def cmd_add(a: argparse.Namespace) -> int:
    d = Path(a.dir)
    d.mkdir(parents=True, exist_ok=True)
    idx = _load(d)
    idx["schema"] = SCHEMA
    idx["project"] = a.project
    if a.iid is not None:
        idx["mr_iid"] = a.iid
    elif idx.get("mr_iid") is None and d.name.isdigit():
        idx["mr_iid"] = int(d.name)
    made = a.made_at or dt.datetime.now().astimezone().isoformat(timespec="seconds")
    key = a.discussion or a.finding
    evidence = [{"mr": idx["mr_iid"], "sha": a.head}] if isinstance(idx.get("mr_iid"), int) else []
    evidence += [{"mr": k, "sha": v} for k, v in _parse_mr_sha(a.evidence).items() if (k, v) != (idx.get("mr_iid"), a.head)]
    blk = d / f"{key}.html"
    said = []
    if blk.exists():
        for draft in extract_drafts(blk.read_text()):
            said += find_promises(draft)
    lst = "entries" if a.discussion else "findings"
    idx.setdefault(lst, [])
    old = next((e for e in idx[lst] if e.get("discussion_id", e.get("finding")) == key), {})
    status = {p["text"]: p.get("status", "queued") for p in old.get("promises", []) if isinstance(p, dict)}
    entry = {
        "head_sha": a.head,
        "tier": a.tier,
        "title": a.title,
        "block": f"{key}.html",
        "deep_page": a.deep,
        "cold_read": a.cold_read,
        "cold_read_note": a.cold_read_note or "",
        "requested_by": a.requested_by,
        "made_at": made,
        "evidence": evidence,
        "promises": [{"text": p, "status": status.get(p, "queued")} for p in said],
    }
    if a.discussion:
        entry = {"discussion_id": a.discussion, **entry, "last_note_id": a.last_note,
                 "anchor_status": a.anchor_status}
    else:
        entry = {"finding": a.finding, **entry, "draft_note_id": a.draft_note}
    idx[lst] = [e for e in idx[lst] if e.get("discussion_id", e.get("finding")) != key]
    idx[lst].append(entry)
    if lst == "findings":
        idx["findings"].sort(key=lambda e: e["made_at"])
    _save(d, idx)
    q = sum(p["status"] == "queued" for p in entry["promises"])
    print(f"index.json: {len(idx['entries'])} entries ({key[:8]} {a.tier}), {q} promise(s) queued")
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    threads = json.loads(Path(a.threads).read_text()) if a.threads else None
    probs, warn = audit(Path(a.dir), a.head, threads, _parse_mr_sha(a.mr_head), a.review, a.overview)
    for p in probs:
        print(p)
    for w in warn:
        print(f"warn: {w}")
    if probs:
        return 1
    idx = json.loads((Path(a.dir) / "index.json").read_text())
    n, m, r = len(idx["entries"]), len(idx.get("findings", [])), len(idx.get("reviews", []))
    ov = f", overview at {idx['overview']['head_sha'][:9]}" if idx.get("overview") else ""
    print(f"ok: {n} entries" + (f", {m} findings" if m else "") + (f", {r} reviews" if r else "") + ov
          + (f", {len(warn)} warnings" if warn else ""))
    return 0


def _sigs(a: argparse.Namespace, project: str, iid: int | None) -> dict[str, str]:
    """Each changed file's diff signature at --head: from --changes, else GitLab (GET): the MR's changes when --head
    is its head now, else the MR version at --head.
    """
    if a.no_sigs:
        return {}
    if a.changes:
        c = json.loads(Path(a.changes).read_text())
        return RI.sigs_of(c.get("changes") or c.get("diffs") or [] if isinstance(c, dict) else c)
    from glab_common import api

    p = f"projects/{project.replace('/', '%2F')}/merge_requests/{iid}"
    if (api(p).get("diff_refs") or {}).get("head_sha") == a.head:
        return RI.sigs_of(api(f"{p}/changes?access_raw_diffs=true").get("changes") or [])
    v = next((v for v in api(f"{p}/versions") or [] if v.get("head_commit_sha") == a.head), None)
    if not v:
        raise SystemExit(f"no MR version at {a.head[:9]}; pass --changes or --no-sigs")
    return RI.sigs_of(api(f"{p}/versions/{v['id']}").get("diffs") or [])


def cmd_review_add(a: argparse.Namespace) -> int:
    d = Path(a.dir)
    d.mkdir(parents=True, exist_ok=True)
    idx = _load(d)
    idx["project"] = idx.get("project") or a.project
    if idx.get("mr_iid") is None:
        idx["mr_iid"] = a.iid if a.iid is not None else int(d.name) if d.name.isdigit() else None
    entry = RI.add_review(d, idx, task=a.task, kind=a.kind, tier=a.tier, head=a.head, verdict=a.verdict,
                          state=a.state, page=Path(a.page), record=a.record, drafts=RI.drafts_from(Path(a.drafts)),
                          file_sigs=_sigs(a, idx["project"], idx["mr_iid"]), made_at=a.made_at)
    probs = RI.problems(d, idx)
    if probs:
        for p in probs:
            print(p)
        return 1
    RI.save_atomic(d / "index.json", idx)
    print(f"index.json: review {a.task} at {a.head[:9]}, {len(entry['drafts'])} drafts, page {entry['page']}")
    return 0


def cmd_overview_add(a: argparse.Namespace) -> int:
    d = Path(a.dir)
    probs, warn = MO.check_dir(d, a.head)
    for w in warn:
        print(f"warn: {w}")
    if probs:
        for p in probs:
            print(p)
        print("not listed: fix the notes and rebuild (mr_overview.py make), then add again")
        return 1
    n = MO.names(a.head)
    rec = json.loads((d / n["data"]).read_text())
    idx = _load(d)
    idx["project"] = idx.get("project") or a.project
    if idx.get("mr_iid") is None:
        idx["mr_iid"] = a.iid if a.iid is not None else int(d.name) if d.name.isdigit() else None
    idx.setdefault("entries", [])
    idx["overview"] = {
        "head_sha": a.head,
        "base_sha": rec["base_sha"],
        "made_at": a.made_at or dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "task": a.task,
        "requested_by": a.requested_by,
        "page": n["page"],
        "data": n["data"],
        "svgs": {"map": n["map"], "flow": n["flow"], "pipe": n["pipe"]},
        "cold_read": a.cold_read,
        "cold_read_note": a.cold_read_note or "",
        "boxes": [{k: b[k] for k in ("id", "kind", "status", "line")} for b in rec["boxes"]],
        "threads": {k: v["box"] for k, v in rec["anchors"].items()},
    }
    probs = overview_problems(d, idx["overview"], a.head)
    if probs:
        for p in probs:
            print(p)
        return 1
    RI.save_atomic(d / "index.json", idx)
    ov = idx["overview"]
    print(f"index.json: overview at {a.head[:9]}, {len(ov['boxes'])} boxes, {len(ov['threads'])} threads, "
          f"cold read {a.cold_read}")
    return 0


def cmd_promises(a: argparse.Namespace) -> int:
    idx = _load(Path(a.dir))
    rows = []
    for e in idx.get("entries", []) + idx.get("findings", []):
        key = e.get("discussion_id", e.get("finding", "?"))
        for p in e.get("promises", []):
            rows.append((key, p.get("status", "?"), p.get("text", "")))
    for key, st, text in rows:
        if a.md:
            print(f"- [ ] !{idx.get('mr_iid')} {key[:8]} ({st}): {text}")
        else:
            print(f"{key[:8]}\t{st}\t{text}")
    if not rows:
        print("no promises listed")
    return 0


class _Text(HTMLParser):
    """Collect visible text; replace each figure by its caption and alt text."""

    BLOCKS = {"p", "li", "h1", "h2", "h3", "h4", "div", "figure", "tr", "blockquote", "pre", "br"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.nfig = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self.skip += 1
        if tag in self.BLOCKS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")
        if tag == "img":
            self.nfig += 1
            alt = dict(attrs).get("alt") or ""
            self.parts.append(f"[figure {self.nfig}: {alt}] ")

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self.skip -= 1
        if tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def cmd_text(a: argparse.Namespace) -> int:
    t = _Text()
    t.feed(Path(a.block).read_text())
    txt = html.unescape("".join(t.parts))
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n\n", txt).strip()
    print(txt)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="add or replace one thread's (or finding's) entry")
    p.add_argument("dir")
    who = p.add_mutually_exclusive_group(required=True)
    who.add_argument("--discussion", help="GitLab discussion id (40 hex)")
    who.add_argument("--finding", help="finding-<n>, for a finding whose draft has no thread yet")
    p.add_argument("--head", required=True)
    p.add_argument("--tier", required=True, choices=TIERS)
    p.add_argument("--title", required=True)
    p.add_argument("--last-note", type=int, default=None, help="id of the thread's last note (mr_discussions.py --json)")
    p.add_argument("--anchor-status", choices=ANCHOR_STATUSES, default=None, help="from mr_discussions.py --json")
    p.add_argument("--evidence", action="append", help="IID=SHA of another MR the block's facts come from (repeat)")
    p.add_argument("--draft-note", type=int, default=None, help="GitLab draft note id (findings)")
    p.add_argument("--deep", default=None)
    p.add_argument("--cold-read", default="pending", choices=VERDICTS)
    p.add_argument("--cold-read-note", default="")
    p.add_argument("--requested-by", default="review", choices=REQUESTERS)
    p.add_argument("--project", default="product/site3d")
    p.add_argument("--iid", type=int, default=None)
    p.add_argument("--made-at", default=None, help="ISO 8601 with offset (default now)")
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("check", help="validate the folder; exit 1 on any problem")
    p.add_argument("dir")
    p.add_argument("--head", default=None, help="require every entry at this head SHA")
    p.add_argument("--threads", default=None, help="mr_discussions.py --json --all output: flag newer notes")
    p.add_argument("--mr-head", action="append", help="IID=SHA current head of another MR in the stack (repeat)")
    p.add_argument("--review", default=None, help="require a review by this task id, its page and titled drafts")
    p.add_argument("--overview", default=None, help="require the overview at this head SHA, its files, safe")
    p.set_defaults(fn=cmd_check)
    ov = sub.add_parser("overview", help="the MR architecture overview").add_subparsers(dest="ocmd", required=True)
    p = ov.add_parser("add", help="list the overview built in DIR (mr_overview.py make)")
    p.add_argument("dir")
    p.add_argument("--head", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--cold-read", default="pending", choices=VERDICTS)
    p.add_argument("--cold-read-note", default="")
    p.add_argument("--requested-by", default="review", choices=OVERVIEW_REQUESTERS)
    p.add_argument("--project", default="product/site3d")
    p.add_argument("--iid", type=int, default=None)
    p.add_argument("--made-at", default=None, help="ISO 8601 with offset (default now)")
    p.set_defaults(fn=cmd_overview_add)
    rv = sub.add_parser("review", help="the reviews of the MR (schema 2)").add_subparsers(dest="rcmd", required=True)
    p = rv.add_parser("add", help="copy a review page in and list the review")
    p.add_argument("dir")
    p.add_argument("--task", required=True)
    p.add_argument("--kind", required=True, choices=RI.KINDS)
    p.add_argument("--tier", required=True, choices=RI.REVIEW_TIERS)
    p.add_argument("--head", required=True)
    p.add_argument("--state", required=True, choices=RI.STATES)
    p.add_argument("--verdict", required=True)
    p.add_argument("--page", required=True, help="the review's HTML briefing")
    p.add_argument("--record", required=True, help="docs/branches/<branch>-review.md")
    p.add_argument("--drafts", required=True, help="JSON list or lines: draft_note.py --json plus kind, finding, title")
    p.add_argument("--changes", default=None, help="saved GitLab changes JSON at --head (default: read GitLab)")
    p.add_argument("--no-sigs", action="store_true", help="no file signatures (the app then trusts the head only)")
    p.add_argument("--project", default="product/site3d")
    p.add_argument("--iid", type=int, default=None)
    p.add_argument("--made-at", default=None, help="ISO 8601 with offset (default now)")
    p.set_defaults(fn=cmd_review_add)
    p = sub.add_parser("promises", help="list the fixes the drafts promise")
    p.add_argument("dir")
    p.add_argument("--md", action="store_true", help="as a markdown checklist for the review record")
    p.set_defaults(fn=cmd_promises)
    p = sub.add_parser("text", help="a block's words for the cold reader")
    p.add_argument("block")
    p.set_defaults(fn=cmd_text)
    a = ap.parse_args()
    if a.cmd == "add" and a.discussion and (a.last_note is None or a.anchor_status is None):
        ap.error("add --discussion needs --last-note and --anchor-status (from mr_discussions.py --json)")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
