#!/usr/bin/env python3
"""The MR review app's address of an MR, a thread, a draft or an issue, and the links a page puts beside GitLab's.

The app (repo /home/ubuntu/src/mr-review, site/app.js `where`, `urlOf` and `route`) lives at APP. Its router reads:
    ?mr=<GitLab MR address>                                  the MR's Overview
    ?mr=..&tab=threads&t=<discussion id>#tr-<discussion id>  one thread, opened in place on Threads
    ?mr=..&tab=threads&show=mine&t=draft-<id>#tr-draft-<id>  a pending draft that starts a new thread, opened (its
                                                             Draft row is listed on the Mine filter only)
    ?issue=<GitLab issue address>                            an issue, read only
A draft reply sits under its thread, so its address is its thread's. The GitLab address is the MR's web page,
https://<HOST>/<project>/-/merge_requests/<iid> (mrr/read.py `which` refuses any other form).

Every link opens in a new tab (target="_blank"): Astra View shows a sent page in a frame that may open a tab but
may not move the top window.

Usage:
    app_link.py mr IID [--project P] [--html|--md]
    app_link.py thread IID DISCUSSION_ID [--project P] [--html|--md]
    app_link.py draft IID DRAFT_ID [--reply-to DISCUSSION_ID] [--project P] [--html|--md]
    app_link.py issue IID [--project P] [--html|--md]
    app_link.py stamp PAGE [--threads THREADS.json] [--drafts DRAFTS.json] [--project P] [--iid N]
        add, in place, the app link beside every GitLab MR or issue link that lacks one, "Open thread in MR app"
        beside every GitLab note link of a thread in THREADS.json (mr_discussions.py --all --json), and turn the
        first "draft <id>" of each draft in DRAFTS.json (draft_note.py --json lines, or a list) into its app link
    app_link.py check PAGE... [--threads ...] [--drafts ...] [--project P] [--iid N]
        exit 1, naming each, when a GitLab MR or issue link has no app link beside it, a thread note link no thread
        link, or (with --iid) the page has no app link to the MR, or a listed draft has none

Standard library only, so any Python runs it.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from glab_common import HOST, PROJECT  # noqa: E402

APP = "https://array.hornbill-gray.ts.net/apps/mr-review/"
GITLAB = f"https://{HOST}"
CLASS = "mr-app"  # every link this module writes carries it, so a page and a check can tell them apart
LINK_ATTRS = f'class="{CLASS}" target="_blank" rel="noopener"'
# (a GitLab MR or issue page, with an optional tab or note: .../-/merge_requests/818/diffs#note_31003)
GITLAB_RE = re.compile(
    rf"^{re.escape(GITLAB)}/(?P<project>[\w.-]+(?:/[\w.-]+)+)/-/(?P<kind>merge_requests|issues)/(?P<iid>\d+)"
    r"(?P<rest>[/?#].*)?$")
ANCHOR_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.IGNORECASE | re.DOTALL)
HREF_RE = re.compile(r"""\bhref\s*=\s*(["'])(?P<href>.*?)\1""", re.IGNORECASE | re.DOTALL)
# (what may sit between a GitLab link and its app link and still count as beside it)
BESIDE_RE = re.compile(r"^(?:\s|&nbsp;|&#160;|[(),;:·|]|&middot;|&#183;)*$")


def _q(gitlab_url: str) -> str:
    return quote(gitlab_url, safe="")


def gitlab_mr(iid: int | str, project: str = PROJECT) -> str:
    """The MR's GitLab page."""
    return f"{GITLAB}/{project}/-/merge_requests/{int(iid)}"


def gitlab_issue(iid: int | str, project: str = PROJECT) -> str:
    """The issue's GitLab page."""
    return f"{GITLAB}/{project}/-/issues/{int(iid)}"


def mr(iid: int | str, project: str = PROJECT) -> str:
    """The MR's Overview in the app."""
    return f"{APP}?mr={_q(gitlab_mr(iid, project))}"


def thread(iid: int | str, discussion_id: str, project: str = PROJECT, show: str = "all") -> str:
    """One thread, opened in place on the app's Threads tab under filter `show` (the row's id is tr-<id>)."""
    d = quote(str(discussion_id), safe="")
    f = "" if show == "all" else f"&show={show}"
    return f"{APP}?mr={_q(gitlab_mr(iid, project))}&tab=threads{f}&t={d}#tr-{d}"


def draft(iid: int | str, draft_id: int | str, reply_to: str | None = None, project: str = PROJECT) -> str:
    """A pending draft: a reply under its thread (`reply_to`, the discussion id), else its own Draft row, which the
    app lists on the Mine filter only (site/app.js `shownOf`)."""
    if reply_to:
        return thread(iid, reply_to, project)
    return thread(iid, f"draft-{int(draft_id)}", project, show="mine")


def issue(iid: int | str, project: str = PROJECT) -> str:
    """The issue in the app (read only)."""
    return f"{APP}?issue={_q(gitlab_issue(iid, project))}"


def parse(url: str) -> dict:
    """What the app's router makes of `url` (site/app.js `route` and `where`): the page and its place.

    Returns:
        {"page": "mr"|"issue"|"list"|"other", "project", "iid", "tab", "t", "show", "hash"}; "other" for an
        address off the app or one whose mr/issue is not a GitLab page of HOST.
    """
    s = urlsplit(url)
    base = urlsplit(APP)
    out = {"page": "other", "project": None, "iid": None, "tab": None, "t": "", "show": "all",
           "hash": unquote(s.fragment)}
    if (s.scheme, s.netloc, s.path) != (base.scheme, base.netloc, base.path):
        return out
    q = {k: v[0] for k, v in parse_qs(s.query, keep_blank_values=True).items()}
    key = "issue" if q.get("issue") else "mr" if q.get("mr") else None
    if key is None:
        out["page"] = "list"
        return out
    m = GITLAB_RE.match(q[key])
    want = "issues" if key == "issue" else "merge_requests"
    if not m or m.group("kind") != want:
        return out
    out.update(page=key, project=m.group("project"), iid=int(m.group("iid")))
    if key == "mr":
        tab = {"file": "files"}.get(q.get("tab", ""), q.get("tab", ""))
        out["tab"] = tab if tab in ("overview", "review", "threads", "files") else (
            "files" if q.get("file") else "overview")
        out["t"] = q.get("t", "")
        out["show"] = q.get("show") if q.get("show") in ("open", "all", "mine", "unread") else "all"
    return out


# --- HTML and markdown ---


def a(href: str, text: str) -> str:
    return f'<a {LINK_ATTRS} href="{html.escape(href)}">{html.escape(text)}</a>'


def mr_html(iid: int | str, project: str = PROJECT) -> str:
    """'!818 (GitLab) and Open in MR app', the pair a page puts wherever it names the MR."""
    ref = f"!{int(iid)}" if project == PROJECT else f"{project}!{int(iid)}"
    return (f'<a href="{html.escape(gitlab_mr(iid, project))}" target="_blank" rel="noopener">{html.escape(ref)}</a>'
            f' {a(mr(iid, project), "Open in MR app")}')


def thread_html(iid: int | str, discussion_id: str, project: str = PROJECT) -> str:
    return a(thread(iid, discussion_id, project), "Open thread in MR app")


def draft_html(iid: int | str, draft_id: int | str, reply_to: str | None = None, project: str = PROJECT) -> str:
    return a(draft(iid, draft_id, reply_to, project), f"Open draft {int(draft_id)} in MR app")


def issue_html(iid: int | str, project: str = PROJECT) -> str:
    ref = f"#{int(iid)}" if project == PROJECT else f"{project}#{int(iid)}"
    return (f'<a href="{html.escape(gitlab_issue(iid, project))}" target="_blank" rel="noopener">{html.escape(ref)}</a>'
            f' {a(issue(iid, project), "Open in MR app")}')


def md(href: str, text: str) -> str:
    return f"[{text}]({href})"


# --- reading a page ---


def anchors(page: str) -> list[dict]:
    """Every <a> of `page`: its span, href (unescaped), class and body."""
    out = []
    for m in ANCHOR_RE.finditer(page):
        h = HREF_RE.search(m.group("attrs"))
        cls = re.search(r"""\bclass\s*=\s*(["'])(.*?)\1""", m.group("attrs"), re.IGNORECASE)
        out.append({"start": m.start(), "end": m.end(), "href": html.unescape(h.group("href")) if h else "",
                    "app": bool(cls and CLASS in cls.group(2).split()), "body": m.group("body")})
    return out


def _target(gl: re.Match) -> str:
    """The app address a GitLab MR or issue link gets beside it."""
    iid, project = gl.group("iid"), gl.group("project")
    return mr(iid, project) if gl.group("kind") == "merge_requests" else issue(iid, project)


def _note_id(gl: re.Match) -> int | None:
    m = re.search(r"#note_(\d+)$", gl.group("rest") or "")
    return int(m.group(1)) if m else None


def _beside(page: str, links: list[dict], i: int, want: str) -> bool:
    """Whether anchor i is followed, with only spaces or punctuation between, by app links, one to `want`."""
    end = links[i]["end"]
    for nxt in links[i + 1:]:
        if not (nxt["app"] and BESIDE_RE.match(page[end:nxt["start"]])):
            return False
        if nxt["href"] == want:
            return True
        end = nxt["end"]
    return False


def note_threads(threads: list[dict] | None) -> dict[int, str]:
    """note id -> discussion id, from mr_discussions.py --all --json output."""
    return {n["id"]: t["id"] for t in threads or [] for n in t.get("notes", []) if "id" in n}


def problems(page: str, threads: list[dict] | None = None, drafts: list[dict] | None = None,
             iid: int | None = None, project: str = PROJECT) -> list[str]:
    """Every missing app link on `page` (see the module docstring, `check`); empty when it has them all."""
    out: list[str] = []
    links = anchors(page)
    by_note = note_threads(threads)
    hrefs = {x["href"] for x in links if x["app"]}
    for i, x in enumerate(links):
        gl = GITLAB_RE.match(x["href"]) if not x["app"] else None
        if not gl:
            continue
        note = _note_id(gl)
        disc = by_note.get(note) if note is not None and gl.group("kind") == "merge_requests" else None
        want = thread(gl.group("iid"), disc, gl.group("project")) if disc else _target(gl)
        if not _beside(page, links, i, want):
            out.append(f"no app link beside {x['href']}" + (f" (thread {disc[:8]})" if disc else ""))
    if iid is not None and mr(iid, project) not in hrefs:
        out.append(f"no link to !{iid} in the MR app ({mr(iid, project)})")
    for d in drafts or []:
        if iid is None:
            break
        want = draft(iid, d["id"], d.get("discussion_id") or d.get("reply_to"), project)
        if want not in hrefs:
            out.append(f"draft {d['id']} has no app link ({want})")
    return out


def stamp(page: str, threads: list[dict] | None = None, drafts: list[dict] | None = None,
          iid: int | None = None, project: str = PROJECT) -> str:
    """`page` with every app link `problems` misses added (idempotent): beside each GitLab link, around the first
    "draft <id>" of each listed draft, and an "Open in MR app" line under the first heading when the page has no
    link to the MR at all (with `iid`)."""
    links = anchors(page)
    by_note = note_threads(threads)
    adds: list[tuple[int, int, str]] = []  # (start, end, replacement), applied from the end
    for i, x in enumerate(links):
        gl = GITLAB_RE.match(x["href"]) if not x["app"] else None
        if not gl:
            continue
        note = _note_id(gl)
        disc = by_note.get(note) if note is not None and gl.group("kind") == "merge_requests" else None
        want = thread(gl.group("iid"), disc, gl.group("project")) if disc else _target(gl)
        if not _beside(page, links, i, want):
            text = "Open thread in MR app" if disc else "Open in MR app"
            adds.append((x["end"], x["end"], f" {a(want, text)}"))
    hrefs = {x["href"] for x in links if x["app"]}
    if iid is not None:
        spans = [(x["start"], x["end"]) for x in links]
        for d in drafts or []:
            want = draft(iid, d["id"], d.get("discussion_id") or d.get("reply_to"), project)
            if want in hrefs:
                continue
            for m in re.finditer(rf"\b[Dd]raft (?:note )?{int(d['id'])}\b", page):
                inside = any(s <= m.start() < e for s, e in spans) or _in_tag(page, m.start())
                if not inside:
                    adds.append((m.start(), m.end(), a(want, m.group(0))))
                    break
    out = page
    for s, e, r in sorted(adds, key=lambda t: t[0], reverse=True):
        out = out[:s] + r + out[e:]
    if iid is not None and mr(iid, project) not in {x["href"] for x in anchors(out) if x["app"]}:
        out = put_under_heading(out, f'<p class="{CLASS}-links">{mr_html(iid, project)}</p>')
    return out


def _in_tag(page: str, i: int) -> bool:
    """Whether position i is inside a tag (an attribute) or a <style>/<code>/<pre> element."""
    if page.rfind("<", 0, i) > page.rfind(">", 0, i):
        return True
    for t in ("style", "script", "code", "pre"):
        if page.lower().rfind(f"<{t}", 0, i) > page.lower().rfind(f"</{t}>", 0, i):
            return True
    return False


def put_under_heading(page: str, snippet: str) -> str:
    """`snippet` after the first </h1> or </h2>, else after <body ...>, else first."""
    m = re.search(r"</h[12]>", page, re.IGNORECASE)
    if not m:
        m = re.search(r"<body[^>]*>", page, re.IGNORECASE)
    i = m.end() if m else 0
    return page[:i] + snippet + page[i:]


def read_drafts(p: str | None) -> list[dict]:
    """draft_note.py --json lines (one object a line) or a JSON list, each with an id."""
    if not p:
        return []
    txt = Path(p).read_text().strip()
    if txt.startswith("["):
        return json.loads(txt)
    return [json.loads(ln) for ln in txt.splitlines() if ln.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, pos in (("mr", []), ("thread", ["discussion_id"]), ("draft", ["draft_id"]), ("issue", [])):
        p = sub.add_parser(name)
        p.add_argument("iid", type=int)
        for x in pos:
            p.add_argument(x)
        if name == "draft":
            p.add_argument("--reply-to", default=None, help="the thread's discussion id, for a draft reply")
        p.add_argument("--project", default=PROJECT)
        fmt = p.add_mutually_exclusive_group()
        fmt.add_argument("--html", action="store_true", help="the HTML link(s) to paste")
        fmt.add_argument("--md", action="store_true", help="a markdown link")
    for name in ("stamp", "check"):
        p = sub.add_parser(name)
        p.add_argument("pages", nargs=1 if name == "stamp" else "+")
        p.add_argument("--threads", default=None, help="mr_discussions.py --all --json output")
        p.add_argument("--drafts", default=None, help="draft_note.py --json lines, or a JSON list")
        p.add_argument("--project", default=PROJECT)
        p.add_argument("--iid", type=int, default=None, help="the page's MR: it must link it in the app")
    a_ = ap.parse_args()
    if a_.cmd in ("stamp", "check"):
        threads = json.loads(Path(a_.threads).read_text()) if a_.threads else None
        drafts = read_drafts(a_.drafts)
        bad = 0
        for p in a_.pages:
            page = Path(p).read_text()
            if a_.cmd == "stamp":
                new = stamp(page, threads, drafts, a_.iid, a_.project)
                if new != page:
                    Path(p).write_text(new)
                n = len(anchors(new)) - len(anchors(page))
                print(f"{p}: {n} app link(s) added")
                page = new
            for x in problems(page, threads, drafts, a_.iid, a_.project):
                print(f"{p}: {x}")
                bad += 1
        if a_.cmd == "check" and not bad:
            print(f"ok: {len(a_.pages)} page(s), every GitLab link has its app link")
        return 1 if bad else 0
    if a_.cmd == "mr":
        url, text, pair = mr(a_.iid, a_.project), "Open in MR app", mr_html(a_.iid, a_.project)
    elif a_.cmd == "thread":
        url, text = thread(a_.iid, a_.discussion_id, a_.project), "Open thread in MR app"
        pair = thread_html(a_.iid, a_.discussion_id, a_.project)
    elif a_.cmd == "draft":
        url, text = draft(a_.iid, a_.draft_id, a_.reply_to, a_.project), f"Open draft {a_.draft_id} in MR app"
        pair = draft_html(a_.iid, a_.draft_id, a_.reply_to, a_.project)
    else:
        url, text, pair = issue(a_.iid, a_.project), "Open in MR app", issue_html(a_.iid, a_.project)
    print(pair if a_.html else md(url, text) if a_.md else url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
