#!/usr/bin/env python3
"""Keep the per-MR thread explanations folder and its index.json (see references/thread-explanation.md).

Layout: site3d_project_root/outputs/mr_context/<iid>/
    <discussion_id>.html   one explanation block per thread (self-contained, dark, figures as data URIs)
    deep_<topic>.html      Deep pages, shared by the threads on one topic
    index.json             one entry per thread, the schema below; the MR review app reads it

Usage:
    mr_context.py add DIR --discussion ID --head SHA --tier short|standard|deep --title TEXT
                  [--deep deep_x.html] [--cold-read pass|rewritten|gap] [--cold-read-note TEXT]
                  [--requested-by button|review|watcher] [--project product/site3d] [--iid N]
    mr_context.py check DIR [--head SHA]   exit 1 unless every entry is valid and its files exist
    mr_context.py text DIR/<discussion_id>.html   the block's words, figures listed by caption (cold read)

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

SCHEMA = 1
TIERS = ("short", "standard", "deep")
VERDICTS = ("pass", "rewritten", "gap", "pending")
REQUESTERS = ("button", "review", "watcher")
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
HEX40 = re.compile(r"^[0-9a-f]{40}$")


def _load(d: Path) -> dict:
    p = d / "index.json"
    if not p.exists():
        return {"schema": SCHEMA, "project": None, "mr_iid": None, "entries": []}
    return json.loads(p.read_text())


def _save(d: Path, idx: dict) -> None:
    idx["entries"].sort(key=lambda e: e["made_at"])
    (d / "index.json").write_text(json.dumps(idx, indent=2) + "\n")


def problems(d: Path, head: str | None = None) -> list[str]:
    """Every way the folder breaks the schema; empty when it is valid.

    Args:
        d: The MR's mr_context folder.
        head: When given, every entry must be at this head SHA.

    Returns:
        One line per problem.
    """
    out = []
    p = d / "index.json"
    if not p.exists():
        return [f"{p}: missing"]
    try:
        idx = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return [f"{p}: not JSON ({e})"]
    if idx.get("schema") != SCHEMA:
        out.append(f"schema is {idx.get('schema')!r}, expected {SCHEMA}")
    if not isinstance(idx.get("mr_iid"), int):
        out.append("mr_iid missing or not an integer")
    if not isinstance(idx.get("project"), str):
        out.append("project missing")
    entries = idx.get("entries")
    if not isinstance(entries, list) or not entries:
        return out + ["entries missing or empty"]
    seen = set()
    for i, e in enumerate(entries):
        tag = f"entry {i} ({e.get('discussion_id', '?')[:8]})"
        for k, t in ENTRY_KEYS.items():
            if k not in e:
                out.append(f"{tag}: no {k}")
            elif not isinstance(e[k], t):
                out.append(f"{tag}: {k} has type {type(e[k]).__name__}")
        extra = set(e) - set(ENTRY_KEYS)
        if extra:
            out.append(f"{tag}: unknown keys {sorted(extra)}")
        if out and out[-1].startswith(tag + ": no"):
            continue
        if not HEX40.match(e["discussion_id"]):
            out.append(f"{tag}: discussion_id is not 40 hex")
        if e["discussion_id"] in seen:
            out.append(f"{tag}: duplicate discussion_id")
        seen.add(e["discussion_id"])
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
        if e["block"] != f"{e['discussion_id']}.html":
            out.append(f"{tag}: block must be <discussion_id>.html")
        elif not (d / e["block"]).exists():
            out.append(f"{tag}: block file {e['block']} missing")
        if e["tier"] == "deep" and not e["deep_page"]:
            out.append(f"{tag}: a deep entry needs deep_page")
        if e["deep_page"] and not (d / e["deep_page"]).exists():
            out.append(f"{tag}: deep page {e['deep_page']} missing")
        try:
            if dt.datetime.fromisoformat(e["made_at"]).utcoffset() is None:
                out.append(f"{tag}: made_at has no UTC offset")
        except ValueError:
            out.append(f"{tag}: made_at is not ISO 8601")
        blk = d / e["block"]
        if blk.exists():
            txt = blk.read_text()
            if "<script" in txt.lower():
                out.append(f"{tag}: block has a script")
            if re.search(r'<img[^>]+src="(?!data:)', txt):
                out.append(f"{tag}: block has an image that is not a data URI")
            if "<img" not in txt and "<svg" not in txt:
                out.append(f"{tag}: block has no figure")
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
    entry = {
        "discussion_id": a.discussion,
        "head_sha": a.head,
        "tier": a.tier,
        "title": a.title,
        "block": f"{a.discussion}.html",
        "deep_page": a.deep,
        "cold_read": a.cold_read,
        "cold_read_note": a.cold_read_note or "",
        "requested_by": a.requested_by,
        "made_at": made,
    }
    idx["entries"] = [e for e in idx["entries"] if e.get("discussion_id") != a.discussion]
    idx["entries"].append(entry)
    _save(d, idx)
    print(f"index.json: {len(idx['entries'])} entries ({a.discussion[:8]} {a.tier})")
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    probs = problems(Path(a.dir), a.head)
    for p in probs:
        print(p)
    if probs:
        return 1
    n = len(json.loads((Path(a.dir) / "index.json").read_text())["entries"])
    print(f"ok: {n} entries")
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
    p = sub.add_parser("add", help="add or replace one thread's entry")
    p.add_argument("dir")
    p.add_argument("--discussion", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--tier", required=True, choices=TIERS)
    p.add_argument("--title", required=True)
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
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("text", help="a block's words for the cold reader")
    p.add_argument("block")
    p.set_defaults(fn=cmd_text)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
