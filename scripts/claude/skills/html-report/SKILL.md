---
name: html-report
description: Build and deliver a self-contained HTML deck (white by default), briefing, report or summary (dark theme; figures as data URIs, no scripts, works offline on a phone), self-review it with screenshots, send it via astra send-doc, and archive it in a dated ppt/ folder with a README. Use whenever the user asks for a deck, slides, report, summary, write-up, briefing, findings, status update or "HTML" deliverable, or when a review or investigation produces results for them to read.
allowed-tools: Read, Bash, Write, Edit, Grep, Glob
---

# HTML report / deck delivery

Any requested summary, write-up, plan, review briefing or deck goes out as ONE self-contained
HTML file sent with `astra send-doc`, never as chat text alone and never as a claude.ai
Artifact. The chat message carries the verdict in a few lines plus the file name.

Files here: `assets/theme-light.css` (decks and slides, the default for them),
`assets/theme.css` (dark: reports, briefings, summaries, and decks only when the user asks for
dark), `assets/deck-template.html` (white deck skeleton), `assets/template.html` (report
skeleton), `scripts/bake_math.py` (typeset MathJax then strip scripts), `scripts/shot_html.py`
(screenshots with JS off, offline; exits 1 if scripts remain). Both themes use the same class
names, so a page switches theme by swapping the pasted stylesheet.

## Build

1. Pick the theme by deliverable, then paste that stylesheet into `<style>` (inline):
   - **Deck or slides: light** (user, Oct 27 2026: "Use that deck style as the default"). Start
     from `assets/deck-template.html` with `assets/theme-light.css`: white background, ink
     `#0F172A` headings, slate `#475569` body, muted `#64748B`, one thin solid accent bar per
     slide (`accent-bar teal|blue|amber|rose`), accents teal `#0D9488` / indigo `#4F46E5` /
     amber `#D97706` / rose `#E11D48`, number cards `.num.<accent>` on the tints `#F0FDFA` /
     `#EEF2FF` / `#FFFBEB` / `#FFF1F2`. Figures with white surrounds: dark figure scripts
     re-skin with `import deck_theme` and `THEME=light` (`site3d_project_root/test_scripts/deck_theme.py`).
   - **Report, briefing, summary, review: dark** (unchanged). Start from `assets/template.html`
     with `assets/theme.css`: `#121212` background, blue/teal/amber/rose accents.
   - Dark deck only when the user asks for it; a light report only when asked.
   Both: Inter body, IBM Plex Mono code, 16 px side gutter, no horizontal scroll at phone width.
2. **Visual first.** Every finding, step or result gets a figure; words are captions of at most
   two sentences. Prefer a measured diagnostic (the actual image/map/scatter, before vs after on
   one scale) over a schematic; a mechanism diagram only when nothing measurable shows it. 2-D
   maps and images, not bar charts or histograms, unless asked. Figures go in as data URIs
   (`base64` of the PNG); resize figures to the width they are shown at.
3. **Self-contained and low-jargon**: define every symbol next to its equation, add a glossary
   when symbols accumulate, worked numeric examples for constructions, say which variant was
   used where and why. Drop abandoned approaches instead of contrasting them. No implementation
   parameters (sampling factors, pair indices, internal notes) in the deliverable unless asked;
   those go in the README. Reserve space the user asks for (e.g. top fifth for their title block).
4. Math: write MathJax inline/display math, then
   `uv run --no-project --with playwright python scripts/bake_math.py in.html out.html`
   to pre-render it and strip every `<script>`. Always with `--no-project` (plain `uv run` drops uv.lock
   and a .venv into whatever worktree it runs in), and never `pixi exec --spec playwright-python` (conda's
   playwright can overwrite its driver in pixi's shared cache).
5. Slide decks use `<section class="slide">` per slide with an accent bar on top and a
   `slide-num` bottom-right. pptx decks (python-pptx, `~/pixi_tools/ppt`) use the same light
   values (reference `site3d_project_root/ppt/2026-10-27-all-hands-sim/scripts/build_deck.py`,
   `THEME=light`) and go out as PDF via `libreoffice --headless`; a deck to be presented also
   goes out as the `.pptx` itself (user, Oct 27 2026: "I need pptx as well to import to google
   slides"), so send both with `astra send-doc`. Strip autoshape theme styles (shadows show in
   LibreOffice and Google Slides) as `no_style()` in that script does.

## Self-review before sending (mandatory)

- `python scripts/shot_html.py FILE OUTDIR [--slides section.slide]` from the scratchpad in the
  playwright pixi env; read every screenshot (resize to 1800 px first). Fix overlaps, cut-off
  labels, garbled ticks, elements hidden behind others.
- Review every figure for CONTENT, not layout: does it show what the caption claims, do the
  values make physical sense, same window/scale/crop on compared panels, current method version,
  identical sign and axis conventions across the deck.
- `shot_html.py` must report zero scripts left (`astra send-doc` refuses a page with a `<script>` tag).

## Send and archive

- `astra send-doc <file> "<caption>"`; captions are Markdown (links as `[label](url)`,
  backtick names with underscores). The page carries the task's figures and files: send it, not
  each figure with `astra send-photo` beside it (one report per task, Oct 27 2026: "a single
  comprehensive html with figures as the report"); a task with one figure sends just that file.
  Caption each figure in its `<figcaption>`: screenshots of a view name the device and theme
  ("phone, dark"), which `astra ship` reads for its shot check.
- Archive with the send: `astra send-doc --archive <descriptive-kebab-title> --preview <png> <file> "<caption>"`
  copies the deliverable and its preview into `site3d_project_root/ppt/YYYY-MM-DD-<title>/`
  (`ASTRA_ARCHIVE_DIR`; resending with the same title updates that folder). Add there the other PNG previews, frozen copies of the generator and every data/figure/check script it
  depends on, the figures and numbers (JSON) it embeds, a patch of any uncommitted code the
  results depend on (`git diff > name.patch`, base commit recorded), and a `README.md` to
  reproduce it cold (what it shows, branch and commit, data and cache paths, commands in order,
  env/GPU/runtime, the parameters behind every number, results, caveats). `ppt/output/` stays
  the working output; update the dated folder whenever the deck changes.
- Times in the file: Pacific, 12-hour, zone label; read the clock
  (`TZ=America/Los_Angeles date "+%-I:%M %p %Z"`), never extrapolate.
