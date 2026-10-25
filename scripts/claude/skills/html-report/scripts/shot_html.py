#!/usr/bin/env python3
"""Screenshot a self-contained HTML deliverable for self-review, with JS disabled and offline.

Usage:
    uv run --no-project --with playwright python shot_html.py FILE OUTDIR [--width 1280]
        [--slides SELECTOR]   screenshot each matching element (e.g. "section.slide") instead of the page

Writes OUTDIR/page_NN.png (viewport-height tiles of the full page) or OUTDIR/slide_NN.png, prints
the count and the number of <script> tags left (should be 0 for a shipped file). Keep `--no-project`:
without it `uv run` treats a worktree as a uv project. Not `pixi exec --spec playwright-python`: conda's
playwright can overwrite its driver in pixi's shared cache (astra 0.194.1).
"""
import argparse
import pathlib
import sys

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("file")
ap.add_argument("outdir")
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--height", type=int, default=900)
ap.add_argument("--slides", default=None, help="CSS selector of per-slide elements")
a = ap.parse_args()
src, out = pathlib.Path(a.file).resolve(), pathlib.Path(a.outdir)
out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": a.width, "height": a.height}, java_script_enabled=False, offline=True)
    pg = ctx.new_page()
    pg.goto(src.as_uri())
    pg.wait_for_timeout(800)
    n_scripts = pg.locator("script").count()
    if a.slides:
        loc = pg.locator(a.slides)
        n = loc.count()
        for i in range(n):
            loc.nth(i).scroll_into_view_if_needed()
            loc.nth(i).screenshot(path=str(out / f"slide_{i + 1:02d}.png"))
        print(f"{n} slides -> {out}")
    else:
        total = pg.evaluate("() => document.documentElement.scrollHeight")
        n = max(1, -(-total // a.height))
        for i in range(n):
            pg.evaluate(f"() => window.scrollTo(0, {i * a.height})")
            pg.wait_for_timeout(100)
            pg.screenshot(path=str(out / f"page_{i + 1:02d}.png"))
        print(f"{n} page tiles ({total}px) -> {out}")
    print(f"scripts left in file: {n_scripts}" + ("" if n_scripts == 0 else "  <-- strip them before sending"))
    b.close()
    sys.exit(0 if n_scripts == 0 else 1)
