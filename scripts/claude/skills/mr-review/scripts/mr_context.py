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
    mr_context.py promises DIR [--md]   the queued fixes the drafts promise ("I'll ...")
    mr_context.py text DIR/<discussion_id>.html   the block's words, figures listed by caption (cold read)

Schema 2 adds last_note_id, anchor_status, evidence and promises to an entry. Schema 1 entries (written
before Nov 20 2026) still pass; their missing keys give a warning.

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
FINDING_OPTIONAL = {"draft_note_id": (int, type(None)), "evidence": list, "promises": list}
INDEX_KEYS = {"schema", "project", "mr_iid", "entries", "findings"}
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
    (d / "index.json").write_text(json.dumps(idx, indent=2) + "\n")


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
) -> tuple[list[str], list[str]]:
    """Every way the folder breaks the schema or the draft rules, and what only deserves a warning.

    Args:
        d: The MR's mr_context folder.
        head: When given, every entry must be at this head SHA.
        threads: `mr_discussions.py --json --all` output; an entry whose thread has a note newer
            than its last_note_id is stale even at the same head.
        mr_heads: Current head per MR iid; evidence taken from that MR at another SHA is stale.

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
    entries = idx.get("entries")
    findings = idx.get("findings", [])
    if not isinstance(entries, list) or not isinstance(findings, list) or not (entries or findings):
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


def problems(d: Path, head: str | None = None) -> list[str]:
    """Every way the folder breaks the schema; empty when it is valid (see `audit`)."""
    return audit(d, head)[0]


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
    probs, warn = audit(Path(a.dir), a.head, threads, _parse_mr_sha(a.mr_head))
    for p in probs:
        print(p)
    for w in warn:
        print(f"warn: {w}")
    if probs:
        return 1
    idx = json.loads((Path(a.dir) / "index.json").read_text())
    n, m = len(idx["entries"]), len(idx.get("findings", []))
    print(f"ok: {n} entries" + (f", {m} findings" if m else "") + (f", {len(warn)} warnings" if warn else ""))
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
    p.set_defaults(fn=cmd_check)
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
