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
- Port maps
- Cold-read check
- File layout and index.json
- Where it shows up

## What an explanation holds (in order)

0. **In the app**: `mr_context.py add` puts the thread's "Open thread in MR app" link (a finding's
   "Open draft <id> in MR app", with `--draft-note`) under the block's first heading, and "Open in MR app"
   beside each GitLab MR link in it (`scripts/app_link.py`); a block made from Nov 25, 2026, 12 PM PST on
   without them fails `check`. Write them in yourself with `app_link.py thread <iid> <discussion id> --html`
   when you want them elsewhere in the block.
1. **Where**: opens with the thread's **mini map** and a link back to the overview
   (`references/mr-overview.md`): inline `overview-<head12>-mini-<disc8>.svg` from the MR's
   `outputs/mr_context/<iid>/` folder (the repo map cut to the thread's file, its box outlined; the
   box is computed from the thread's own position by `mr_overview.py`), then
   `<a href="#card-<slug>">See it on the overview</a>` (the slug of the box id, the card the
   overview page and the review briefing carry). A thread on no box (a general comment naming no
   function) gets no mini map and says "the MR as a whole". Then `file:line` at the head SHA (a
   GitLab blob link pinned to the SHA), the function, its
   caller chain two levels up, and what flows in and out (shapes, units, frames). Take the line from
   `mr_discussions.py --all --json`, which keeps the thread as the reviewer saw it: the full GitLab
   position, the head the MR had when the comment was posted (`posted_head`) and every note. When
   `anchor_status` is `inferred`, GitLab moved the position to a later version after a restack (the
   API keeps no original position): read `inferred_line`, which gives the anchored line number at the
   moved position and at the posted head and which of the two holds the names the comment quotes;
   explain the posted line and say "inferred" beside the anchor. Read every note: a reply can narrow,
   answer or withdraw the root comment (a reviewer resolving their own question six minutes later),
   and the last note decides what is still asked.
2. **The idea at stake, from zero**: the convention, formula or design choice the comment turns on,
   every symbol defined where it first appears. A reader who never saw the code must follow it.
3. **Your history**: who wrote the line and when, the commit message, and every record that names
   the topic, in the order of "Sources for Jisu's history" below (the fact log first, then
   `scripts/line_origin.py`). Quote the decision with its date; write "no record" when there is
   none, and say where you looked.
4. **Checked**: the comment's claim tested against the code by a small script, with the number it
   gives and how it was measured. Verdict in one word: right, wrong, or right but moot, and why.
   Every decision claim (one the verdict, an option or the draft rests on) carries its tested
   conditions beside it (see "Cold-read check"). Evidence names where it came from as
   `file:line @ <sha> (!<iid>)`: code read at an upstack tip belongs to the MR that holds it
   (`line_origin.py --mr IID=BASE..HEAD` names it), not to the MR under review.
5. **Options**: each possible response (change the code, push back, defer to an issue, answer only),
   with what it changes downstream (callers, other MRs in the stack, tests, results).
6. **Draft reply** last: a GitLab draft in team language under the posting rules
   (`references/posting-glab.md`). It lives in the block, in an element with class `draft` (or
   `reply`); nothing is posted unless the run is a review that posts drafts. A placeholder Jisu must
   fill is written `[JISU: <what to decide>]` (the check warns); any other `[...]` slot left in a
   draft fails the check. Each "I'll ..." or "we will ..." sentence is a promise: `mr_context.py add`
   lists it in the entry's `promises` as `queued`, and `mr_context.py promises <dir> --md` gives the
   fix list; mark it `done` or `dropped` in `index.json` when settled.

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
- The "Jisu said" fact log, `~/.config/astra/areas/1a1c25b6/facts.md` (the desk appends Jisu's own
  words that settle a thread, one line each: date, symbols and topic words, the quote, where said). A
  matching line decides the history; cite it first.
- `python ~/.claude/skills/mr-review/scripts/line_origin.py --repo <repo> --rev <posted head> --file
  <path> --line <n> --comment "<root note>" [--mr IID=BASE..HEAD ...]`: blame at the posted head; when
  blame stops at a split's port commit (author time exactly 12:00:00, or listed in a port map), it
  follows the function to the dev line (`origin/main..jisu/sar_apps`: the commit that introduced
  `def <name>`, the commits that touched it, the first commit that added the line's text or names,
  and a name hard-coded earlier elsewhere on the dev line), then searches the session transcripts
  (`~/.claude*/projects/**/*.jsonl`, read only) for the function name and for each found commit's
  subject, which names the session and task that wrote it (subjects survive restacks, SHAs do not).
  About 1 to 4 s per line; `--batch LINES.json --out DIR` for a whole MR. Open the listed sessions for
  the sentence that made the choice; the tool lists the five oldest hits, so search the session
  folder for the topic words when those do not hold it.
- `git log -L` / `git blame` on the anchored lines; the commit message (when blame reaches a real
  commit, not a port).
- The branch notes `site3d_project_root/docs/branches/<branch>.md` and the review record
  `<branch>-review.md` (findings, dispositions, ground-truth facts).
- `site3d_project_root/research/`, `summary/`, `projects/`, and
  `research/future-considerations.md` for anything about phase, timing or known approximations.
- Claude memory entries that name the topic (`MEMORY.md` index, then the file).
- The MR description, linked issues, earlier threads on the same lines or the same MR (resolved
  ones too: `mr_discussions.py --all`), and the other MRs in the stack (`gs ls`).
- `astra results "<topic words>"` in the area.

## Port maps

A split that ports dev-line work into MR branches writes one JSON per split, at split time, to
`/home/ubuntu/src/site3d_project_root/outputs/port_maps/<YYYY-MM-DD>-<split>.json` (the split brief
owns writing it; `line_origin.py` reads every file there by default):

```json
{
  "schema": 1,
  "made": "2026-11-07T12:00:00-08:00",
  "task": "T-xx",
  "source": {"branch": "jisu/sar_apps", "tip": "<40 hex dev tip ported from>"},
  "commits": [
    {"port": "<40 hex port commit>", "mr": 782, "branch": "jisu/tomo-solvers",
     "sources": ["<dev commits whose code it carries>"],
     "paths": {"<path on the MR>": "<path on the dev line>"}}
  ]
}
```

With a map, a port commit is known by its SHA, not only by its 12:00:00 author time, and a moved
file is followed to its dev path. Without one the tracer still works by time of day and function
name.

## Cold-read check

1. A fresh subagent gets only the block's words (`scripts/mr_context.py text <block>`), the Deep page's
   words when the block links one, and the figure PNGs, copied into one folder. No code, no thread
   history, no review file, none of your own answers.
2. It answers four questions with the prompt below, copied verbatim (`<dir>` is that folder): what
   does the comment ask; is the reviewer right; what are the options; does each claim hold beyond
   the case it was tested on.
3. Compare with your own answers. ASK, RIGHT and OPTIONS match and SCOPE says "none": verdict `pass`.
   A mismatch, or a SCOPE line: rewrite the block once where the reader went wrong (narrow the claim
   to what was tested, or test the wider case and say so), rerun the check with a new subagent; a
   match then is `rewritten` (note what was missing). A second mismatch: ship the block with verdict
   `gap` and the gap named in `cold_read_note` and at the top of the block, so Jisu knows where it is
   thin.
4. The verdict goes in `index.json`. The desk reads the verdicts; a gap seen twice becomes a rule
   here.

The cold-reader prompt:

```
You are a cold reader testing whether a written explanation of a code-review comment stands on its own. Read ONLY the files in <dir> (explanation.txt, any deep_page.txt, and every PNG figure there, opened with the Read tool). Do not open any other file, code, repository or web page, and do not run commands other than listing that folder.

The explanation is about one reviewer comment on a merge request. From the files alone, answer in at most 300 words, in exactly this format:

ASK: what the comment asks, in one or two sentences.
RIGHT: is the reviewer right? (yes / no / partly) and why, in one or two sentences, with the key number if the text gives one.
OPTIONS: the options the author has, one line each, and which one the explanation recommends.
SCOPE: list every claim the verdict, the recommendation or the draft reply rests on that says more than the text shows was tested. For each: quote its words, name the case the text says it was tested on, and say how the wording reaches beyond it (a word like "every", "always", "never" or "any" drawn from one sample or one setting; a general rule drawn from one case; a result the text itself calls open, stopped, partial or pending but states as settled; a use the text claims for a case, a test or a number that its own numbers show it cannot serve). One line per claim. Write "none" if every claim stays within what was tested.
UNCLEAR: anything else you could not decide or had to guess from these files alone (a term not defined, a figure that does not match its caption, a claim with no support). Write "none" if nothing.
```

Writing so the SCOPE question passes: every decision claim (one the verdict, an option or the draft
reply rests on) carries its tested conditions beside it, in the same sentence or the next: "in 600
noise-only columns of the !783 arc, every column kept an atom", not "every column keeps an atom". A
claim meant to hold in general says why (a proof, a counterexample search) or is narrowed. A result
the record calls open or stopped is quoted as open.

## File layout and index.json

```
site3d_project_root/outputs/mr_context/<iid>/
  <discussion_id>.html   the block: self-contained, html-report dark theme inline, figures as
                         base64 data URIs, no script, readable at 390 px wide
  deep_<topic>.html      Deep pages (same rules), beside the blocks; deep_<topic>.md beside each
  <discussion_id>_fig<N>.png  the figures as files (cold reader, app thumbnails)
  reviews/<head12>-<task>/   each review round's HTML briefing and the files it links (review add)
  index.json
```

Any project other than product/site3d uses `outputs/mr_context/<group>-<project>/<iid>/`.

A finding the review raises that has no GitLab thread yet uses `finding-<n>` in place of a
discussion id for its file name only, and is not listed in `index.json` until its draft becomes a
thread.

`index.json` (schema 2). The MR review app reads it; keep it this small:

```json
{
  "schema": 2,
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
      "made_at": "<ISO 8601 with offset, e.g. 2026-11-19T23:40:00-08:00>",
      "last_note_id": 30134,
      "anchor_status": "as-posted | inferred | general",
      "evidence": [{"mr": 783, "sha": "<40 hex head of another MR read for this block>"}],
      "promises": [{"text": "<the I'll ... sentence>", "status": "queued | done | dropped"}]
    }
  ],
  "findings": [
    {"finding": "finding-1", "head_sha": "...", "tier": "...", "title": "...", "block": "finding-1.html",
     "deep_page": null, "cold_read": "...", "cold_read_note": "", "requested_by": "review",
     "made_at": "...", "draft_note_id": 123,
     "severity": "significant", "path": "a/b.py", "line_from": 81, "line": 83}
  ],
  "reviews": [
    {"task": "T-uZ", "kind": "new | rereview | received-reply | self-review",
     "tier": "quick | standard | deep", "head_sha": "<40 hex>", "made_at": "<ISO 8601 with offset>",
     "state": "request-changes | ready | comments-only", "verdict": "<one line>",
     "page": "reviews/<head12>-<task>/index.html", "page_bytes": 1086549,
     "record": "docs/branches/<branch>-review.md",
     "file_sigs": {"<path>": "<sha1 of the file's diff at head_sha, 16 hex>"},
     "drafts": [{"id": 3939, "kind": "finding | reply | summary", "finding": "finding-1 or null",
                 "discussion_id": "<40 hex, a reply's thread> or null", "path": "a/b.py",
                 "line_from": 81, "line": 83, "title": "<one line>",
                 "posted_sha256": "<sha256 of the body GitLab returned>"}]}
  ]
}
```

Schema 1 entries (before Nov 20 2026) still pass `check`, with a warning that they are fresh by head
only. A finding's block is listed under `findings` until its draft becomes a thread.

- Write entries with `python ~/.claude/skills/mr-review/scripts/mr_context.py add <dir> --discussion
  <id> --head <sha> --last-note <id> --anchor-status <s> --tier <t> --title "<t>" [--evidence
  IID=SHA ...] [--deep deep_x.html] --cold-read <v> [--cold-read-note "<n>"] --requested-by <who>`
  (`--finding finding-<n>` in place of `--discussion` for a finding); it replaces an entry of the
  same id, records the MR's own head as evidence, and queues the draft's promises (keeping the status
  of one already listed).
- Validate with `mr_context.py check <dir> [--head <sha>] [--threads <threads.json>] [--mr-head
  IID=SHA ...]` (exit 1 on any problem: missing field, missing file, a block with a script or a
  non-data-URI image or no figure, a Deep entry without its page, an unfilled `[...]` slot in a draft,
  a promise not in the entry's list, a thread with a note newer than `last_note_id`, an evidence SHA
  that is no longer that MR's head). A review's verify runs it.
- Freshness is the head SHA and the last note id together: an entry is stale when the MR head moved
  or when the thread has a note newer than `last_note_id` (`--threads` takes
  `mr_discussions.py --all --json`). A reply at the same head, such as a withdrawal, makes it stale
  too. The app says "written at an older version" and offers a refresh; the digest and the watcher
  reuse an entry only when both match and never file a new explanation for it.

## The review itself (`reviews`)

Each review round adds one item to `reviews` (newest last), written only by `mr_context.py review
add` (SKILL.md step 8): it copies the round's HTML briefing into `reviews/<head12>-<task>/`, keeps
the drafts the round posted (from `draft_note.py --json`: id, place, `posted_sha256`) with their
titles, and the file signatures at the review head (`file_sigs`, read from GitLab, GET only). A
finding's `severity`, `path`, `line_from` and `line` let the app draw it on its lines.

- The app tells each draft's state from the MR it already reads: not published (still a draft;
  "edited" when its body's hash changed), published, published but rewritten, or deleted.
- When the MR head is not the review's head the app shows a box saying the MR moved; a finding stays
  on its lines only while its file's diff signature is the one in `file_sigs`.
- `check <dir> --review <task>` exits 1 unless that review is listed, its page is under `reviews/`
  with no script, and every draft has a title and a body hash (or the text posted, for a backfill).
- `index.json` is replaced through a temporary file and a rename, so the app never reads half a file.

## Where it shows up

- The review's HTML briefing (`references/html-briefing.md`): in "Threads and draft replies", each
  thread's block sits under the quoted comment and before the draft reply; each finding's block
  under its card.
- The MR review app: the block collapsed as "Explanation" under the thread, from `index.json`; the
  Review tab lists the newest review with its drafts and findings, and opens its page as "Full report".
  The app reads the folder at `/apps/mr-context/` (registered by the app's `register.sh`).
- A pilot or standalone explain run: one page for Jisu (Report pages standard), one item per thread
  with the comment quoted, its block inline (or the Deep page linked) and the cold-read verdict.
