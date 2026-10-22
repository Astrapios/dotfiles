# The HTML briefing (Standard and Deep tiers; Quick only when a real bug turned up)

The chat summary is a few lines; this file is the primary deliverable, read on a phone.

## Contents
- Where it goes
- Page structure
- Figures first
- Self-review before sending
- Send

## Where it goes

`site3d_project_root/docs/branches/<branch>-review-<YYYY-MM-DD>.html` (shared dir, outside
the worktrees). Build it with the html-report skill (`~/.claude/skills/html-report`): same
dark theme, self-contained, no JS, figures as data URIs.

## Page structure (adapt to what the round did)

- Header: MR number, branch, date (Pacific, 12-hour), reviewed and fix commit SHAs, tier.
- A 2-3 line verdict box (what is broken, what holds) and a one-line-per-finding table
  (id, severity pill, title), opening with the count.
- One card per finding: severity pill and title, the figure, a caption of at most two
  sentences saying what to look at, and a one-line fix (inline code only for a one-liner).
  No diff blocks unless the fix itself is the point.
- Pre-existing findings in their own short section after the MR's own.
- If fixes were applied: test and lint results, commits, and (if restacked) what was ported
  into which upstack MR, including merge traps caught.
- "Where things stand": what is pending on the user (submit draft replies), deferred questions.

## Figures first

Every bug gets a descriptive figure; words are captions. Prefer a MEASURED diagnostic (real
traced or simulated data as an image, map or scatter; before vs after or across parameter
values on one scale) over a schematic; a mechanism diagram only when nothing measurable shows
it. Findings with no possible visual (a naming nit) go in one short list at the end. No bar
charts or histograms.

The verification scripts of step 4b live in `site3d_project_root/test_scripts/<branch>_review_figs.py`
with output in `outputs/<branch>_review/`, so the next round re-renders the same diagnostics.

## Self-review before sending

1. Each figure for content first (resize, read it): does it show what the caption claims, are
   readouts sampled at the right place, labels true for the data plotted, same scale across
   compared panels, pixel size not finer than the sampling.
2. The rendered page: `python ~/.claude/skills/html-report/scripts/shot_html.py <file> <outdir>`
   (playwright in a pixi exec env, never uv), read the screenshots, fix overlaps, meaningless
   labels, wrong claims.

## Send

`astra send-doc <file> "<caption>"`, and each figure with
`astra send-photo <png> "<id> <severity>: <one line>"` in the same turn so the user can scan
the findings without opening the HTML. Never a claude.ai Artifact.
