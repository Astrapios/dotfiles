---
name: html-report
description: Build and deliver a self-contained dark-theme HTML deck, briefing, report or summary (figures as data URIs, no scripts, works offline on a phone), self-review it with screenshots, send it via astra send-doc, and archive it in a dated ppt/ folder with a README. Use whenever the user asks for a deck, slides, report, summary, write-up, briefing, findings, status update or "HTML" deliverable, or when a review or investigation produces results for them to read.
allowed-tools: Read, Bash, Write, Edit, Grep, Glob
---

# HTML report / deck delivery

Any requested summary, write-up, plan, review briefing or deck goes out as ONE self-contained
HTML file sent with `astra send-doc`, never as chat text alone and never as a claude.ai
Artifact. The chat message carries the verdict in a few lines plus the file name.

Files here: `assets/theme.css` (inline it), `assets/template.html` (skeleton),
`scripts/bake_math.py` (typeset MathJax then strip scripts), `scripts/shot_html.py`
(screenshots with JS off, offline; exits 1 if scripts remain).

## Build

1. Start from `assets/template.html` with `assets/theme.css` pasted into `<style>`: `#121212`
   background, Inter body, IBM Plex Mono code, blue/teal/amber/rose accents, thin accent bar,
   16 px side gutter, no horizontal scroll at phone width.
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
   `pixi exec --spec python=3.12 --spec playwright-python -- python scripts/bake_math.py in.html out.html`
   to pre-render it and strip every `<script>`. Never `uv run` (it drops uv.lock and a .venv
   into whatever worktree it runs in).
5. Slide decks use `<section class="slide">` per slide with a slide number bottom-right. pptx
   decks (python-pptx, `~/pixi_tools/ppt`) go out as PDF via `libreoffice --headless`, never raw
   .pptx; the same theme values apply.

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
  backtick names with underscores). Send key figures with `astra send-photo` in the same turn.
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
