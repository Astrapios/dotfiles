#!/usr/bin/env python3
"""The live check of app links: each MR review app link of a page (or each address given) loaded on the MR review
app at phone width (390 x 844), read only.

For a page (--page, repeatable) it opens the page itself at 390 px (page-<name>.png), taps its first app link and
checks the app opens in a new tab at that address, then loads every app link the page holds. Each link,
read as app_link.parse reads it, must draw: an MR, its heading "!<iid>" and the tab it names selected; a thread or
a draft, its row (#tr-<id>) on Threads, opened in place; an issue, its heading "#<iid>". One screenshot per link
(link-NN.png) and result.json go to --out.

Read only: every write, marks, explain, gs and desk send the pages ask for is stopped in this browser and counted, and
must be none. It only loads addresses; it taps nothing in the app.

    uv run --no-project --with playwright==1.64.1 python tests/live_app_links.py --out <folder> \
        [--page <page.html> ...] [<app address> ...]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import app_link as AL  # noqa: E402

PHONE = {"width": 390, "height": 844}
WRITES = re.compile(r".*/(write|marks|explain|gs)(\?.*)?$|.*/\.astra/send(\?.*)?$")


def check_link(pg, url: str, shot: Path) -> dict:
    """Load app address `url` in `pg` and check the app drew what it names."""
    p = AL.parse(url)
    r = {"url": url, "page": p["page"], "iid": p["iid"], "tab": p["tab"], "t": p["t"], "ok": False}
    pg.goto(url)
    try:
        if p["page"] == "mr":
            pg.wait_for_selector(".mrhead h1", timeout=90000)
            r["heading"] = pg.inner_text(".mrhead h1").strip()
            r["selected_tab"] = pg.eval_on_selector(".tabs .tab[aria-selected='true']", "e => e.textContent.trim()")
            ok = f"!{p['iid']}" in r["heading"] and r["selected_tab"].lower().startswith(p["tab"][:4])
            if p["t"]:
                sel = f"[id='tr-{p['t']}']"
                pg.wait_for_selector(sel, timeout=60000)
                row = pg.locator(sel)
                r["row"] = row.locator(".trow-h").first.inner_text().strip().splitlines()[:2]
                r["opened"] = row.locator(".trow-h").first.get_attribute("aria-expanded") == "true"
                top = row.evaluate("e => e.getBoundingClientRect().top")
                r["row_on_screen"] = 0 <= top < PHONE["height"]
                ok = ok and r["opened"] and r["row_on_screen"]
            r["ok"] = ok
        elif p["page"] == "issue":
            pg.wait_for_selector(".ihead h1", timeout=90000)
            r["heading"] = pg.inner_text(".ihead h1").strip()
            r["ok"] = f"#{p['iid']}" in r["heading"]
        else:
            r["error"] = "not an MR, thread, draft or issue address of the app"
    except Exception as e:  # noqa: BLE001  (a missing element: say which and keep checking the rest)
        r["error"] = str(e).splitlines()[0][:300]
    pg.wait_for_timeout(600)
    pg.screenshot(path=str(shot))
    r["shot"] = shot.name
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("urls", nargs="*", help="app addresses to load")
    ap.add_argument("--page", action="append", default=[], help="a page whose app links are loaded")
    ap.add_argument("--out", required=True, help="folder for the screenshots and result.json")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stopped: list[str] = []
    res = {"pages": [], "links": [], "stopped_writes": stopped}
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport=PHONE, device_scale_factor=2, is_mobile=True, has_touch=True)

        def stop(route):
            stopped.append(f"{route.request.method} {route.request.url}")
            route.abort()

        ctx.route(WRITES, stop)
        urls = list(a.urls)
        for page in a.page:
            f = Path(page).resolve()
            hrefs = [x["href"] for x in AL.anchors(f.read_text()) if x["app"]]
            pr = {"page": str(f), "app_links": len(hrefs), "tapped": None}
            pg = ctx.new_page()
            pg.goto(f.as_uri())
            pg.wait_for_timeout(500)
            pr["sideways_scroll"] = pg.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            pg.screenshot(path=str(out / f"page-{f.stem}.png"))
            first = pg.locator("a.mr-app").first
            if first.count():
                first.scroll_into_view_if_needed()
                pg.screenshot(path=str(out / f"page-{f.stem}-link.png"))
                with ctx.expect_page(timeout=30000) as new:
                    first.tap()
                tab = new.value
                tab.wait_for_selector(".mrhead h1, .ihead h1", timeout=90000)
                href = first.get_attribute("href")
                pr["tapped"] = {"href": href, "opened": tab.url, "same": tab.url == href,
                                "heading": tab.inner_text(".mrhead h1, .ihead h1").strip()}
                tab.wait_for_timeout(600)
                tab.screenshot(path=str(out / f"page-{f.stem}-tap.png"))
                tab.close()
            pg.close()
            res["pages"].append(pr)
            urls += [h for h in hrefs if h not in urls]
        pg = ctx.new_page()
        for i, u in enumerate(urls, 1):
            res["links"].append(check_link(pg, u, out / f"link-{i:02d}.png"))
        b.close()
    (out / "result.json").write_text(json.dumps(res, indent=1) + "\n")
    bad = [x for x in res["links"] if not x["ok"]]
    bad_pages = [p for p in res["pages"] if not (p["tapped"] or {}).get("same") or p["sideways_scroll"]]
    for x in res["links"]:
        print(("ok   " if x["ok"] else "FAIL ") + f"{x['page']} {x.get('iid')} {x.get('t') or x.get('tab') or ''}: "
              + (x.get("heading", "") if x["ok"] else x.get("error", json.dumps(x)[:300])))
    for p in res["pages"]:
        print(f"page {Path(p['page']).name}: {p['app_links']} app links, tapped -> "
              f"{(p['tapped'] or {}).get('heading')}, sideways scroll {p['sideways_scroll']}")
    print(f"{len(res['links']) - len(bad)} of {len(res['links'])} links drawn; writes stopped: {len(stopped)}")
    return 1 if bad or bad_pages or stopped else 0


if __name__ == "__main__":
    sys.exit(main())
