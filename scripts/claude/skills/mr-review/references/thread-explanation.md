# Thread explanations: background for each MR comment

Jisu, Nov 19 2026: "big friction in mr review is that I am working on gazillion things so I forget
exact details. When someone comments, or if I ask to review an MR, give a background on the
comments so that I have enough context to review before submiting." And: "for each comment, I want
"explain this with details" button or something to fill me in details and context for me to
understand the problem. Most of these should use fig and diagrams to explain as much as possible".

Every open thread on the MR gets one explanation, and so does every finding the review raises. It
is written so Jisu can decide the thread without opening the code. Three things ask for one: a
review (step 2 and step 7), the MR review app's Explain button, and the comment watcher; all three
write the same files.

## Contents
- What an explanation holds (in order)
- Tiers and how to pick one
- Figures first
- Sources for Jisu's history
- Cold-read check
- File layout and index.json
- Where it shows up

## What an explanation holds (in order)

1. **Where**: `file:line` at the head SHA (a GitLab blob link pinned to the SHA), the function, its
   caller chain two levels up, and what flows in and out (shapes, units, frames).
2. **The idea at stake, from zero**: the convention, formula or design choice the comment turns on,
   every symbol defined where it first appears. A reader who never saw the code must follow it.
3. **Your history**: who wrote the line and when (`git log -L <a>,<b>:<file>`, blame), the commit
   message, and every record that names the topic (list below). Quote the decision with its date;
   write "no record" when there is none, and say where you looked.
4. **Checked**: the comment's claim tested against the code by a small script, with the number it
   gives and how it was measured. Verdict in one word: right, wrong, or right but moot, and why.
5. **Options**: each possible response (change the code, push back, defer to an issue, answer only),
   with what it changes downstream (callers, other MRs in the stack, tests, results).
6. **Draft reply** last: a GitLab draft in team language under the posting rules
   (`references/posting-glab.md`). It lives in the block; nothing is posted unless the run is a
   review that posts drafts.

## Tiers and how to pick one

| Tier | For comments about | Holds | Figures |
|---|---|---|---|
| Short | naming, style, docstrings, typos, a stale sentence | items 1 and 6, one sentence of 2, a one-line check | one small diagram of where the code sits or what the text should say |
| Standard | logic, interfaces, tests, performance, a factual question about the code | all six items | one or two, measured from the real code |
| Deep | physics, conventions, frames, geometry, signal model, phase | all six items in the block, plus a linked Deep page | the page: three to five measured figures (3-D where the topic is geometric) and its own `.md` of numbers and method |

- Pick by what a correct answer needs, not by how the comment is phrased: a "small question" about a
  phase sign is Deep; a long comment about a test fixture is Standard.
- A comment that spans tiers takes the higher one.
- State the tier in the block with a one-line reason; the desk audits the choice.
- Several threads on one topic share one Deep page (each block links the same page and keeps its
  own items 1, 4, 5, 6).
- Model Deep page: `~/.config/astra/areas/1a1c25b6/runs/T-t_k/ecef_along_track.html` (+ `.md`):
  measured 3-D geometry from the code at a pinned SHA, numbers with their method, code links at the
  SHA.

## Figures first

- At least one figure per explanation. Measured from the code where possible (run the function on
  the real inputs, a real dictionary, a real formation, the real test); a mechanism diagram only
  when nothing measurable shows it.
- Every figure comes from a script `site3d_project_root/test_scripts/mr<iid>_explain_*.py`, one
  command per result, named as `replay:`. GPU-touching checks run inside
  `astra job run --gpu-mem 1G -- ...`; CPU checks set `CUDA_VISIBLE_DEVICES=''` and assert NumPy ran.
- Code is read from a detached worktree of the MR head (`git worktree add --detach <path> <sha>`),
  never from a checkout of the MR branch, and nothing is committed to it. Name the SHA each fact
  comes from when it is not the MR head (a caller in another MR of the stack).
- The figure caption says what to look at in one line. Same scales across compared panels. Look at
  every figure for content before it ships (html-report self-review).

## Sources for Jisu's history

In this order, quoting what each says about the topic, with its date:
- `git log -L` / `git blame` on the anchored lines; the commit message.
- The branch notes `site3d_project_root/docs/branches/<branch>.md` and the review record
  `<branch>-review.md` (findings, dispositions, ground-truth facts).
- `site3d_project_root/research/`, `summary/`, `projects/`, and
  `research/future-considerations.md` for anything about phase, timing or known approximations.
- Claude memory entries that name the topic (`MEMORY.md` index, then the file).
- The MR description, linked issues, earlier threads on the same lines or the same MR (resolved
  ones too: `mr_discussions.py --all`), and the other MRs in the stack (`gs ls`).
- `astra results "<topic words>"` in the area.

## Cold-read check

1. A fresh subagent gets only the block's words (`scripts/mr_context.py text <block>`), the Deep page's
   words when the block links one, and the figure PNGs. No code, no thread history, no review file.
2. It answers three questions: what does the comment ask; is the reviewer right; what are the options.
3. Compare with your own answers. All three match: verdict `pass`. A mismatch: rewrite the block once
   where the reader went wrong, rerun the check with a new subagent; a match then is `rewritten`
   (note what was missing). A second mismatch: ship the block with verdict `gap` and the gap named
   in `cold_read_note` and at the top of the block, so Jisu knows where it is thin.
4. The verdict goes in `index.json`. The desk reads the verdicts; a gap seen twice becomes a rule
   here.

## File layout and index.json

```
site3d_project_root/outputs/mr_context/<iid>/
  <discussion_id>.html   the block: self-contained, html-report dark theme inline, figures as
                         base64 data URIs, no script, readable at 390 px wide
  deep_<topic>.html      Deep pages (same rules), beside the blocks; deep_<topic>.md beside each
  <discussion_id>_fig<N>.png  the figures as files (cold reader, app thumbnails)
  index.json
```

A finding the review raises that has no GitLab thread yet uses `finding-<n>` in place of a
discussion id for its file name only, and is not listed in `index.json` until its draft becomes a
thread.

`index.json` (schema 1). The MR review app reads it; keep it this small:

```json
{
  "schema": 1,
  "project": "product/site3d",
  "mr_iid": 782,
  "entries": [
    {
      "discussion_id": "<40 hex GitLab discussion id>",
      "head_sha": "<40 hex MR head the explanation was written at>",
      "tier": "short | standard | deep",
      "title": "<one line, what the thread is about, plain words>",
      "block": "<discussion_id>.html",
      "deep_page": "deep_<topic>.html or null",
      "cold_read": "pass | rewritten | gap | pending",
      "cold_read_note": "<what the cold reader missed; empty on pass>",
      "requested_by": "button | review | watcher",
      "made_at": "<ISO 8601 with offset, e.g. 2026-11-19T23:40:00-08:00>"
    }
  ]
}
```

- Write entries with `python ~/.claude/skills/mr-review/scripts/mr_context.py add <dir> --discussion
  <id> --head <sha> --tier <t> --title "<t>" [--deep deep_x.html] --cold-read <v>
  [--cold-read-note "<n>"] --requested-by <who>`; it replaces an entry of the same discussion id.
- Validate with `mr_context.py check <dir> [--head <sha>]` (exit 1 on any problem: missing field,
  missing file, a block with a script or a non-data-URI image or no figure, a Deep entry without its
  page). A review's verify runs it.
- An entry whose `head_sha` is not the MR's current head is "written at an older version": the app
  says so and offers a refresh; the digest and the watcher reuse an entry at the current head and
  never file a new explanation for it.

## Where it shows up

- The review's HTML briefing (`references/html-briefing.md`): in "Threads and draft replies", each
  thread's block sits under the quoted comment and before the draft reply; each finding's block
  under its card.
- The MR review app: the block collapsed as "Explanation" under the thread, from `index.json`.
- A pilot or standalone explain run: one page for Jisu (Report pages standard), one item per thread
  with the comment quoted, its block inline (or the Deep page linked) and the cold-read verdict.
