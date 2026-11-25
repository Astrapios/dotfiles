"""Tests for scripts/app_link.py and the app links each page builder writes (overview, explanation block, review page).

Every app link a builder writes is read by the MR review app's own router: site/app.js's `where` and `route`, run in
node as they are in the app's source (/home/ubuntu/src/mr-review, or MR_REVIEW_APP), and its GitLab address by the
app's own read action check (mrr/read.py `which` / `which_issue`). The live load (the app on array, read only) is
tests/live_app_links.py.

Run: pixi run --manifest-path <site3d>/pyproject.toml -e test pytest tests --deselect tests/test_glab_host.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))
import app_link as AL  # noqa: E402
from test_mr_overview import fx, good_notes, mo, repo, threads  # noqa: E402,F401  (the overview's fixture MR)

APP_ROOT = Path(os.environ.get("MR_REVIEW_APP", "/home/ubuntu/src/mr-review"))
APP_JS = APP_ROOT / "site" / "app.js"
DISC = "a" * 40
HEAD = "c" * 40
SITE3D = "product/site3d"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# --- the app's own router, in node ---


def _block(src: str, start: str) -> str:
    """The source from `start` to its matching closing brace."""
    i = src.index(start)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        depth += {"{": 1, "}": -1}.get(src[k], 0)
        if depth == 0:
            return src[i:k + 1]
    raise AssertionError(f"no end to {start}")


def _line(src: str, start: str) -> str:
    i = src.index(start)
    return src[i:src.index(";\n", i) + 1]


HARNESS = r"""
const SRC = %s;
let PAGE = null;
const issuePage = () => { PAGE = 'issue'; }, mrPage = () => { PAGE = 'mr'; }, browsePage = () => { PAGE = 'view'; };
const listPage = () => { PAGE = 'list'; }, drawMenu = () => {}, draw = () => { PAGE = 'mr'; };
const S = null, BROWSE = {};
let location = null;
eval(SRC);
const out = [];
for (const u of JSON.parse(require('fs').readFileSync(0, 'utf8'))) {
  const url = new URL(u);
  location = { search: url.search, hash: url.hash, pathname: url.pathname };
  PAGE = null;
  route(0);
  const q = new URLSearchParams(url.search);
  out.push({ url: u, page: PAGE, mr: q.get('mr'), issue: q.get('issue'), where: PAGE === 'mr' ? where() : null,
             hash: decodeURIComponent(url.hash.slice(1)) });
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def router():
    """Read app links as the app does: {url: {page, mr, issue, where: {tab, file, view, show, t}, hash}}."""
    node = shutil.which("node")
    assert node, "node is needed to run the app's router"
    assert APP_JS.is_file(), f"the MR review app's page is not at {APP_JS} (set MR_REVIEW_APP)"
    src = APP_JS.read_text()
    parts = [_line(src, "const TABS ="), _line(src, "const OLD_TABS ="), _line(src, "const SHOWS ="),
             _block(src, "function where()"), _block(src, "function route(")]
    harness = HARNESS % json.dumps("\n".join(parts))

    def read(urls: list[str]) -> dict:
        r = subprocess.run([node, "-e", harness], input=json.dumps(urls), capture_output=True, text=True, check=True)
        return {x["url"]: x for x in json.loads(r.stdout)}

    read.src = src
    return read


@pytest.fixture(scope="module")
def read_action():
    """The app's read action's own address checks (mrr/read.py)."""
    sys.path.insert(0, str(APP_ROOT))
    from mrr import read as R

    return R


def _resolve(router, read_action, urls: list[str]) -> dict:
    """Each app link as the app reads it; asserts it lands on an MR or an issue the read action accepts."""
    got = router(urls)
    for u, x in got.items():
        assert x["page"] in ("mr", "issue"), (u, x)
        if x["page"] == "mr":
            x["which"] = read_action.which({"mr": x["mr"]})
        else:
            x["which"] = read_action.which_issue({"issue": x["issue"]})
    return got


def _thread_lands(router, x: dict, t: str) -> None:
    """A thread link opens thread `t` in place on Threads and scrolls to its row, as the app's rows are named, under
    a filter that lists the row: a new-thread draft's Draft row only on Mine (site/app.js `shownOf`)."""
    assert x["where"]["tab"] == "threads" and x["where"]["t"] == t, x
    assert x["hash"] == f"tr-{t}", x
    assert 'id="tr-${esc(t.id)}"' in router.src   # (the app's row id: a thread's and a new-thread draft's)
    shown = re.search(r"const shownOf = k => \(k === 'mine' \? \[(?P<mine>[^\]]*)\] : (?P<other>[^;]*);", router.src)
    assert shown, "the app's shownOf changed: read which filter lists a draft row"
    if t.startswith("draft-"):
        assert "draftRows()" in shown["mine"] and x["where"]["show"] == "mine", x
    else:
        assert "draftRows()" not in shown["other"] and x["where"]["show"] in ("all", "open"), x


def app_hrefs(page: str) -> list[str]:
    return [x["href"] for x in AL.anchors(page) if x["app"]]


# --- app_link itself ---


def test_addresses_resolve_in_the_app(router, read_action):
    urls = {"mr": AL.mr(818), "thread": AL.thread(818, DISC), "new": AL.draft(818, 4060),
            "reply": AL.draft(818, 4061, DISC), "issue": AL.issue(147), "mti": AL.mr(1, "product/mti")}
    got = _resolve(router, read_action, list(urls.values()))
    assert got[urls["mr"]]["which"] == (SITE3D, 818) and got[urls["mr"]]["where"]["tab"] == "overview"
    _thread_lands(router, got[urls["thread"]], DISC)
    _thread_lands(router, got[urls["new"]], "draft-4060")
    assert "id: `draft-${d.id}`" in router.src   # (the app's row of a pending new-thread draft)
    assert got[urls["new"]]["where"]["show"] == "mine"
    assert got[urls["thread"]]["where"]["show"] == "all"
    _thread_lands(router, got[urls["reply"]], DISC)   # (a draft reply sits under its thread)
    assert got[urls["issue"]]["which"] == (SITE3D, 147)
    assert got[urls["mti"]]["which"] == ("product/mti", 1)


def test_parse_matches_the_app_router(router):
    urls = [AL.mr(818), AL.thread(818, DISC), AL.draft(5, 7), AL.issue(9), AL.APP, AL.APP + "?view=mrs"]
    got = router(urls)
    for u in urls:
        p, x = AL.parse(u), got[u]
        assert p["page"] == ({"view": "other"}.get(x["page"], x["page"])), (u, p, x)
        if x["page"] == "mr":
            assert (p["tab"], p["t"], p["show"]) == (x["where"]["tab"], x["where"]["t"], x["where"]["show"])


def test_gitlab_link_gets_its_app_link_beside_it():
    gl = AL.gitlab_mr(818)
    page = f'<h1>R</h1><p>MR <a href="{gl}">!818</a> by Y; see <a href="{AL.gitlab_issue(147)}">#147</a>.</p>'
    assert len(AL.problems(page)) == 2
    once = AL.stamp(page)
    assert AL.problems(once) == []
    assert AL.stamp(once) == once   # (idempotent)
    assert app_hrefs(once) == [AL.mr(818), AL.issue(147)]
    # a link to a note of a known thread gets its thread
    note = f'<p><a href="{gl}#note_31003">Cassie</a></p>'
    th = [{"id": DISC, "notes": [{"id": 31003}]}]
    assert app_hrefs(AL.stamp(note, threads=th)) == [AL.thread(818, DISC)]
    assert AL.problems(AL.stamp(note), threads=th)   # (an MR link there is not the thread's)


def test_stamp_links_drafts_and_the_mr():
    page = "<html><body><h1>Review</h1><ul><li>Draft 4060 on a.py:3.</li><li>Draft 4061 reply.</li></ul></body></html>"
    drafts = [{"id": 4060, "discussion_id": None}, {"id": 4061, "discussion_id": DISC}]
    assert len(AL.problems(page, drafts=drafts, iid=818)) == 3
    out = AL.stamp(page, drafts=drafts, iid=818)
    assert AL.problems(out, drafts=drafts, iid=818) == []
    assert set(app_hrefs(out)) == {AL.mr(818), AL.draft(818, 4060), AL.thread(818, DISC)}
    assert out.index(AL.mr(818).replace("&", "&amp;")) < out.index("<ul>")   # (the MR line under the title)


def test_cli_prints_each_form():
    run = [sys.executable, str(SCRIPTS / "app_link.py")]
    assert subprocess.run([*run, "mr", "818"], capture_output=True, text=True, check=True).stdout.strip() == AL.mr(818)
    h = subprocess.run([*run, "thread", "818", DISC, "--html"], capture_output=True, text=True, check=True).stdout
    assert app_hrefs(h) == [AL.thread(818, DISC)] and 'target="_blank"' in h
    m = subprocess.run([*run, "draft", "818", "4060", "--md"], capture_output=True, text=True, check=True).stdout
    assert m.strip() == f"[Open draft 4060 in MR app]({AL.draft(818, 4060)})"


# --- each page builder ---


def test_overview_page_links_the_mr_in_the_app(mo, repo, tmp_path, router, read_action):  # noqa: F811
    fxx = mo.facts(repo["path"], repo["base"], repo["head"])
    out = tmp_path / "818"
    mo.build(out, fxx, good_notes(), threads(repo), repo["path"], SITE3D)
    page = (out / mo.names(fxx["head"])["page"]).read_text()
    assert AL.problems(page, iid=818) == []
    got = _resolve(router, read_action, app_hrefs(page))
    assert [x["which"] for x in got.values()] == [(SITE3D, 818)]
    probs, _ = mo.check_dir(out, fxx["head"])
    assert probs == []
    # embedded in a briefing, the links come along
    brief = tmp_path / "brief.html"
    brief.write_text("<html><body><h1>Review</h1><p>x</p></body></html>")
    mo.embed(out, fxx["head"], brief)
    assert AL.mr(818) in app_hrefs(brief.read_text())
    # another project's folder, named by --iid
    other = tmp_path / "product-mti" / "1"
    mo.build(other, fxx, good_notes(), None, repo["path"], "product/mti", iid=1)
    assert app_hrefs((other / mo.names(fxx["head"])["page"]).read_text()) == [AL.mr(1, "product/mti")]


def _block_page() -> str:
    return ("<!doctype html><html><body><h2>Why times two?</h2><svg></svg>"
            f'<p>See <a href="{AL.gitlab_mr(806)}">!806</a>.</p><div class="draft">Right, fixed.</div></body></html>')


def _add(mc, d: Path, *who: str, made: str = "2026-11-26T09:00:00-08:00") -> None:
    sys.argv = ["mr_context.py", "add", str(d), *who, "--head", HEAD, "--tier", "short", "--title", "t",
                "--last-note", "100", "--anchor-status", "as-posted", "--made-at", made, "--project", SITE3D]
    assert mc.main() == 0


def test_explanation_block_links_its_thread_and_draft(tmp_path, router, read_action):
    mc = _load("mr_context")
    d = tmp_path / "818"
    d.mkdir()
    (d / f"{DISC}.html").write_text(_block_page())
    (d / "finding-1.html").write_text(_block_page())
    _add(mc, d, "--discussion", DISC)
    _add(mc, d, "--finding", "finding-1", "--draft-note", "4060")
    blk, fnd = (d / f"{DISC}.html").read_text(), (d / "finding-1.html").read_text()
    got = _resolve(router, read_action, app_hrefs(blk) + app_hrefs(fnd))
    _thread_lands(router, got[AL.thread(818, DISC)], DISC)
    _thread_lands(router, got[AL.draft(818, 4060)], "draft-4060")
    assert got[AL.mr(806)]["which"] == (SITE3D, 806)   # (the GitLab link to another MR, beside it)
    probs, _ = mc.audit(d, HEAD)
    assert not [p for p in probs if "app link" in p]


def test_check_refuses_a_new_block_without_links_and_warns_on_an_old_one(tmp_path):
    mc = _load("mr_context")
    for made, where in (("2026-11-26T09:00:00-08:00", 0), ("2026-11-20T09:00:00-08:00", 1)):
        d = tmp_path / made[:10] / "818"
        d.mkdir(parents=True)
        (d / f"{DISC}.html").write_text(_block_page())
        _add(mc, d, "--discussion", DISC, made=made)
        (d / f"{DISC}.html").write_text(_block_page())   # (written again by hand, without the links)
        got = mc.audit(d, HEAD)
        assert [p for p in got[where] if "app link" in p], got
        assert not [p for p in got[1 - where] if "app link" in p], got


def test_review_page_copy_links_the_mr_and_each_draft(tmp_path, router, read_action):
    mc = _load("mr_context")
    d = tmp_path / "818"
    page = tmp_path / "brief.html"
    page.write_text(f'<html><body><h1>Review of !818</h1><p>MR <a href="{AL.gitlab_mr(818)}">!818</a>.</p>'
                    "<ul><li>Draft 4060 on a.py:3: fix it.</li><li>Draft 4061 replies to Cassie.</li></ul></body></html>")
    drafts = tmp_path / "drafts.json"
    drafts.write_text(json.dumps([
        {"id": 4060, "kind": "finding", "finding": "finding-1", "discussion_id": None, "path": "a.py", "line": 3,
         "title": "Fix it", "posted_sha256": "0" * 64},
        {"id": 4061, "kind": "reply", "finding": None, "discussion_id": DISC, "path": None, "line": None,
         "title": "Reply", "posted_sha256": "1" * 64}]))
    sys.argv = ["mr_context.py", "review", "add", str(d), "--task", "T-x", "--kind", "new", "--tier", "standard",
                "--head", HEAD, "--state", "ready", "--verdict", "Fine.", "--page", str(page), "--record", "r.md",
                "--drafts", str(drafts), "--no-sigs", "--made-at", "2026-11-26T09:00:00-08:00", "--project", SITE3D]
    assert mc.main() == 0
    copy = (d / f"reviews/{HEAD[:12]}-T-x/index.html").read_text()
    got = _resolve(router, read_action, app_hrefs(copy))
    assert set(got) == {AL.mr(818), AL.draft(818, 4060), AL.thread(818, DISC)}
    _thread_lands(router, got[AL.draft(818, 4060)], "draft-4060")
    _thread_lands(router, got[AL.thread(818, DISC)], DISC)
    probs, _ = mc.audit(d, review="T-x")
    assert not [p for p in probs if "app link" in p], probs
    # a page that never names a draft is refused, saying which
    (d / f"reviews/{HEAD[:12]}-T-x/index.html").write_text(re.sub(r"<ul>.*</ul>", "", copy))
    probs, _ = mc.audit(d, review="T-x")
    assert [p for p in probs if "draft 4060 has no app link" in p], probs
