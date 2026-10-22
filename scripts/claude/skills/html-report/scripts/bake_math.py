"""Load the deck in headless Chromium, let MathJax typeset, strip every <script>, save a JS-free static HTML."""
import sys, pathlib, re
from playwright.sync_api import sync_playwright
src, dst = pathlib.Path(sys.argv[1]).resolve(), pathlib.Path(sys.argv[2])
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1280, "height": 900})
    pg.goto(src.as_uri())
    pg.evaluate("() => MathJax.startup.promise")
    pg.wait_for_timeout(1500)
    n_math = pg.evaluate("() => document.querySelectorAll('mjx-container').length")
    pg.evaluate("() => document.querySelectorAll('script').forEach(s => s.remove())")
    html = pg.content()
    b.close()
html = re.sub(r'<html lang="en"><head>', '<html lang="en"><head>', html)
dst.write_text(html)
print(f"typeset {n_math} equations, wrote {dst} ({dst.stat().st_size/1e6:.1f} MB)")
