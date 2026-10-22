---
name: mr-review
description: Review and summarize a merge request (MR/PR) for the site3d repo — coding style (repo conformance), soundness, and logic. Verifies data-flow contracts against the codebase, then fans out domain-expert reviewers (SAR physics, computer vision, etc.) seeded with verified ground truth. Use when the user says "review this MR", "/mr-review", or asks to summarize/critique a branch's changes.
allowed-tools: Read, Bash, Grep, Glob, Agent, Write, Edit
---

# MR / Branch Review

Summarize and review the changes on the current branch (or a specified one) against
`main`, checking **coding style (repo conformance), soundness, and logic**. Fan out
domain-expert reviewers for the parts of the diff that touch their domain.

The guiding principle: **establish ground truth before delegating.** A reviewer agent
that re-derives API shapes and coordinate conventions from scratch will often get them
wrong and waste its budget. Verify the contracts yourself first, then hand the agents
verified facts so they spend their effort on judgment, not archaeology.

## Steps

### 0. Load prior review state (incremental re-review)

Reviews are persisted per branch (step 8) at
`site3d_project_root/docs/branches/<branch-with-slashes-as-dashes>-review.md`
(e.g. `jisu/range_compress_gpu` -> `docs/branches/jisu-range_compress_gpu-review.md`).
FIRST check whether that file exists for the branch under review:

- **If it exists**, read it. It records the last-reviewed SHA, the verified ground-truth
  facts, the findings and their dispositions, and the reviewer-comment checklist with
  draft replies. Then review **incrementally**:
  - Diff only what changed since the recorded SHA
    (`git diff <last-reviewed-sha>..HEAD`) plus any files the new commits touch.
  - Re-pull the MR discussions (step 2) and process only comments that are **new or
    newly unresolved** since the recorded state; carry forward prior dispositions.
  - Reuse the recorded ground truth; re-verify a fact only if the new diff touches the
    module it came from.
  - Skip or narrowly scope the domain agents (step 5) to the new delta — do NOT re-run
    a full fan-out over already-reviewed code unless the user asks for a full re-review.
  - **Round cap**: count the fix-and-rereview rounds in the review file's history. After
    the third round on the same MR, stop iterating on findings and say so: a change that
    has not converged in three rounds has an approach problem, not a bug list. Name the
    approach problem and ask the user to reconsider it instead of posting round four.
- **If it doesn't exist** (or the user asks for a full re-review), run the full flow
  below.

### 1. Scope the diff

- `git branch --show-current`, `git log --oneline -8`, and find the merge base:
  `git merge-base HEAD main`. The MR is everything from the merge base to HEAD.
- If branches are stacked, run `gs ls` FIRST to understand stack topology, and review
  only the commits owned by this branch (not the whole stack).
- `git show --stat <range>` / `git diff --stat <merge-base>..HEAD` for the file list.
- **Include `experimental/` and `test_scripts/`** — the user's sims and per-user
  pipeline code live there; never exclude them.

### 1b. Pick the review depth (scale scrutiny to blast radius)

Not every MR earns the full pipeline. Classify by what the diff touches, never by who
wrote it, state the tier in the report, and run only the steps the tier calls for:

- **Quick** — docs, comments, test-only or config-only changes, renames, additive
  tooling under `experimental/` or `test_scripts/` with no production import. Steps
  1-3, step 6 (ruff + dead-parameter sweep), a short chat summary, step 8. No fan-out,
  no refuter, no figures, no HTML briefing unless a real bug turns up.
- **Standard** — ordinary production changes. The full flow, with domain agents only
  for the domains the diff actually touches.
- **Deep** — kernels, geometry and coordinate conventions, phase-sensitive products
  (BP, PFA, sim kernels, InSAR, DOF), range gating and pulse timing, anything the MR
  description calls "exact", "conservative" or "no-op", and any diff over ~1000 lines.
  The full flow plus the refuter and the brute-force checks of step 4b for every claim.

A Quick-tier MR that turns out to touch a Deep-tier path is promoted on the spot; say so
in the report. Large diffs almost always carry findings and small ones usually come
back clean, so do not pad a clean Quick review with nits to justify the run.

### 2. Pull the MR and reviewer comments (glab)

`glab` is authenticated against the GitLab instance. If an MR exists for the branch,
pull its description and reviewer discussion — they are review inputs, not decoration:

- `glab mr view <number>` (or `glab mr view <branch>`) — title, description, reviewers.
  The description is the author's claimed contract; verify the diff against it.
- `glab mr view <number> --comments` — full comment bodies. **This view omits file/line
  anchors, resolved status, and can silently drop comments** — always cross-check with:
- `glab api "projects/:id/merge_requests/<number>/discussions?per_page=100"` — the
  authoritative list. Each note carries `position.new_path`/`new_line` (file/line
  anchor), `resolved`, and `system` (filter system notes out). Parse with a short
  python3/jq snippet rather than eyeballing raw JSON.

Build a checklist of **open (unresolved) reviewer comments**. Seed relevant ones into
the domain agents in step 4 (e.g. hand the SAR reviewer an open question about spectral
edge handling). Resolved threads are context, not action items.

### 3. Read the full diff

Read every changed file in full (not just the hunks) — context around a change is where
bugs hide. For a new file, read the whole thing.

### 4. Establish ground truth (do this BEFORE launching agents)

For each external symbol the diff depends on (types, dataclass fields, array shapes,
coordinate/axis conventions, units, what a producer actually writes vs. what its
docstring claims), open the source and confirm:

- Dataclass field names, shapes, dtypes, and `[row,col]` vs `[x,y]` ordering.
- Normalization/units (e.g. linear magnitude vs dB; [0,1] vs raw).
- Whether a docstring matches the code that produces the data — **trust the code, not
  the docstring**, and note any mismatch.
- Repo conventions from CLAUDE.md: no `__init__.py` (implicit namespace packages, import
  full path), `jsonargparse.CLI` + `autoinit_logger()` for scripts, logging not print,
  Google docstring convention, ruff line length 100, `dev` pixi env for running things.

Write these confirmed facts down — they become the seed for the agents.

**Separate FACTS from CLAIMS.** A fact is read directly from code (a shape, a field
name, what a producer writes). A claim is anything DERIVED — a margin argument, a
convexity/monotonicity/sign property, "this bound is conservative", "this error is just
summation order", "this op is a harmless no-op". Facts seed the domain agents as trusted
ground truth; claims are hypotheses that go to step 4b and to the refuter agent in
step 5, explicitly labeled as targets to attack. Never let your own analysis ride into
agent prompts disguised as verified fact — in the sim_range_cull review an inverted
margin-side claim survived two review rounds exactly this way until an external
reviewer caught it.

### 4b. Adversarially verify every safety/bound claim (numerically, never by prose)

The review must run a REFUTER, not a prover. For every claim of the form
"conservative", "never clips", "always safe", "exact", "no-op", "negligible" — whether
it appears in the diff's comments/docstrings, the MR description, or YOUR OWN step-4
analysis — do the following before writing it into the report:

- **Brute-force counterexample search**: write a small numpy script (scratchpad,
  minutes) that sweeps the relevant geometry/parameter space and checks the claim
  against ground truth computed the slow, obvious way (e.g. evaluate at EVERY pulse,
  every block). The claim is verified only when the search finds no violation AND a
  deliberately weakened version of the code DOES violate — a search that cannot fail
  proves nothing. (The sim_range_cull margin claim fell to a 30-line script in
  15 minutes; the prose derivation had the exposed side exactly backwards.)
- **Analyze the code's literal expression, not a physical proxy**: establish
  convexity/monotonicity/sign for the exact quantity the code computes (e.g.
  `(tau - sample_t_start[p]) * fs`, where the WINDOW also moves per pulse), not the
  physically adjacent quantity (range vs pulse). Differences of convex functions are
  not convex; a property true of the physics can be false of the expression.
- **Asymmetry smell**: any pad/margin/clamp/tolerance applied to ONE side of a
  structurally symmetric problem gets an explicit answer to "why not the other side,
  and which side does the failure mode actually attack?" — derived numerically, not
  argued.
- **Residual-error budget**: when a measured error is attributed to a benign cause
  ("float summation order"), bound what that cause CAN produce at the given dtype and
  operand count. A measured error orders of magnitude above the bound is an unexplained
  mechanism — chase it. (7e-5 booked as fp32 summation order was partly real echo
  clipping; the signal was sitting in the review's own numbers.)
- **Constructive alternative**: for each approximation the diff introduces, spend one
  explicit thought on "is there a cheap exact alternative?" (a log-N bisection vs a
  sampled envelope). If one exists and is small, prototype or propose it; if its known
  weakness can be neutralized cheaply (e.g. UNION its result with the existing bound so
  it can only tighten), say so instead of deferring.
- **Every check renders its diagnostic figure**: write the verification scripts so they
  save the intuitive picture of the result, not just printed numbers (see 7b). One
  script under `site3d_project_root/test_scripts/<branch>_review_figs.py`, output to
  `outputs/<branch>_review/`. Good forms: the actual image/map before vs after on a
  shared scale (e.g. a SAR image per parameter value), a scatter of where points land vs
  where they should, the failing geometry drawn from real traced data, a
  pass/fail matrix for test blind spots. No bar charts or histograms.

### 5. Fan out domain-expert reviewers (parallel, single message)

Pick reviewers by what the diff actually touches, and launch them concurrently in ONE
message (multiple Agent tool uses):

- **SAR / radar physics** → `sar-physics-reviewer` agent: phase history, image formation
  (backprojection/PFA), motion comp, range compression, coordinate transforms, SAR
  phenomenology (layover, double-bounce, shadow, speckle, dB vs linear), geolocation
  conventions.
- **Computer vision / image processing** → `general-purpose` agent framed as a CV expert:
  OpenCV/numpy correctness, morphology, geometry, dense matching/warps, multi-view
  consistency, blending, dtype/shape contracts.
- **Other domains as needed** → `general-purpose` framed appropriately (e.g. GPU/cupy,
  AWS/cloud workflow, data pipeline/DB). Use `Explore` for pure "where/how is X used"
  questions.
- **Claim refuter (ALWAYS, whenever step 4 produced any claims)** → one
  `general-purpose` agent whose ONLY job is to attack. Hand it the fact list, the
  CLAIM list (labeled as claims under attack, never as ground truth), and the key
  hunks; instruct it to construct concrete counterexamples (numeric scripts allowed in
  its scratchpad), to hunt for the weakest claim first, and to report a null result as
  "attacked X via Y, held" — never as praise. This agent is the antidote to
  author-reviewer blindness: when you reviewed code you helped write, it is the only
  reviewer not marinated in your framing.

In each agent prompt:
- Give the absolute file path(s) and tell it to read them fully.
- **Seed it with the verified FACTS from step 4** (shapes, conventions, contracts) and
  tell it to trust those and NOT re-investigate them. Do NOT seed derived claims as
  facts — either omit them or pass them explicitly marked "unverified claim".
- **Scope it to its domain only** ("you cover CV, another reviewer covers SAR physics")
  so reviews don't overlap or contradict.
- Ask for **line-referenced findings with a severity** (OK / minor / significant / bug)
  and to distinguish genuine bugs from style nits.
- Tell it **not to edit any files** — review only.
- Calibrate severity to context: `experimental/` scripts and one-off tools get more
  latitude than production pipeline code, but still surface real physics/logic errors.

### 6. Mechanical checks

- `pixi run -e lint ruff check <changed files or .>` — confirm lint passes.
- If the change has tests, note whether they exist / would run; don't ask the user to
  run speculative things — verify logic yourself.
- **Dead-parameter sweep**: for every function/kernel signature the diff touches, check
  that every parameter is still READ in the new body — not just that the call site's
  argument order matches. A culled loop that stops reading `n_targets` leaves a live
  arg feeding a dead param, which reads as fine in both files separately.
- **Load-bearing-op sweep**: for each defensive-looking operation in or adjacent to the
  hunks (clip, clamp, max/min guard, re-sort, dtype cast), ask whether the API contract
  already guarantees it (`np.searchsorted` returns values in [0, n], so a following
  `np.clip(..., 0, n)` is a no-op). Dead guards mislead readers about what can happen.
- **Test blind-spot audit**: for each new/changed test, state which failure modes of
  the changed code CANNOT fail it BY CONSTRUCTION (e.g. a 3-pulse geometry makes
  envelope sampling exhaustive, so interior-sag clipping is untestable there). Each
  blind spot must be either covered by another test or reported as a finding with a
  concrete test recipe — an in-test comment documenting the hole is not coverage.
- **Fresh-eyes nit pass** over the hunks as a first-time reader: unexplained naming
  conventions (underscore-prefixed locals), unqualified performance numbers in comments
  ("~3-4x" with no geometry caveat), locale spellings ("metres"), stale references to
  removed code. These are cheap to fix and expensive when an external reviewer has to
  ask.
- **Re-litigate wontfixes**: a prior round's "kept deliberately" disposition is not
  settled. Re-test it against repo convention (a config field vs an env var: processing
  history, discoverability, unit-testability) and state explicitly why it survives or
  falls this round.
- **Flag the class, not the instance**: when a finding is a pattern (a dead guard, a
  missing dtype cast, a locale spelling, an unqualified number in a comment), grep the
  whole diff for every occurrence before writing it up and report ONE finding listing
  all locations. An author who fixes the one line you pointed at and leaves the other
  four costs a full extra round.

### 6b. Confirm every finding before it is reported

Agent output is a lead, not a finding. Before a finding enters the report:

- **Reproduce it yourself** against the code: open the `file:line`, trace the failing
  input to the wrong output, and write the concrete failure scenario (inputs and state
  -> wrong result) in one sentence. A finding whose scenario you cannot construct is
  dropped, or at most listed as "unconfirmed" at the end, never in the main table. When
  two agents disagree, the code decides, not the more confident prose.
- **Tag pre-existing problems**: for each confirmed finding check the merge-base
  version of the file (`git blame`, `git show <merge-base>:<path>`). A bug the MR did
  not introduce (this skill reads whole files, so it surfaces old code constantly) is
  tagged **pre-existing**, listed in its own short section, and never blamed on the
  author or raised as a blocker. Post it on the MR only when the MR touches the same
  lines or the user asks; otherwise it is a follow-up note in the branch file. The
  Claude Code team measured that about 59 % of what their reviewer kept re-flagging was
  old code, which is what stops review rounds from converging.
- **Drop cleanup-only output**: a finding that is pure style, diagnostics or
  refactoring preference with no failure scenario goes to the style section of step 7
  or the nit list, never into the soundness table.

### 7. Synthesize the review

Write a concise, skimmable report (do not just paste the agents' raw output). The user
judges by eye and does not want dense text: the chat summary is a short list (one line
per finding: severity, what breaks, fix), and the detail lives in the figures (7b), sent
in the same turn. Covering:

- **Summary** — what `main` gains from this MR, in 2-4 sentences. Describe what the code
  does, not the branch's refactoring journey.
- **Soundness & logic** — the substantive findings, grouped and de-duplicated across
  agents, each with a severity, a `file:line` reference and its one-sentence failure
  scenario (6b). Open with the count ("2 bugs, 1 significant, 3 minor; 2 pre-existing
  kept separate"), then lead with the most important. Pre-existing findings go in their
  own short list after the MR's own, never mixed in.
- **Style / repo conformance** — ruff result, convention adherence, docstring/comment
  issues, naming.
- **Reviewer comments** (when an MR with discussion exists) — for each **open** comment:
  is the reviewer right, is it already addressed in the diff, and what's the recommended
  response (fix / push back with reason / acknowledge). Flag any open comment the
  current diff silently ignores.
- **Verdict** — a short bottom line (e.g. "logic is sound; main concern is X; safe to
  merge as an experimental tool" or "blocking bug at file:line").

Be direct about real problems and don't pad with praise. If you found no bugs, say so
plainly. Note documentation/comment mismatches (e.g. a stale module docstring describing
an abandoned approach) — they mislead future readers even when the code is correct.

### 7b. Deliver an HTML briefing (Standard and Deep tiers)

In addition to the chat summary, produce a self-contained HTML briefing of the review
and send it via `astra send-doc` (never a claude.ai Artifact) for every Standard or Deep
review, and for a Quick review that found a real bug. The user reads this on a phone; it
is the primary deliverable of the review.

- **Location**: `site3d_project_root/docs/branches/<branch>-review-<YYYY-MM-DD>.html`
  (shared dir, outside the worktrees).
- **Style**: dark theme matching the user's deck convention (`#121212` background,
  Inter body / IBM Plex Mono code, blue/teal/amber/rose accents, thin accent bar).
  Static, self-contained, no external assets, no JS.
- **Visual-first (the user's standing preference)**: every bug/problem gets a
  descriptive figure; words are captions, not paragraphs. Prefer a MEASURED diagnostic
  (real traced/simulated data rendered as an image, map, or scatter; before vs after or
  across parameter values on the same scale) over a schematic; use a mechanism diagram
  (boxes/arrows, geometry drawn from real data) only when nothing measurable shows it.
  Findings without any possible visual (a naming nit) go in one short list at the end.
  (Lesson 2026-09-30, MR !675: the first briefing was diff blocks plus "Why:" paragraphs;
  the user said it was too dense and took too much of their time, and asked for figures
  per problem.)
- **Content** (adapt to what the round actually did):
  - Header: MR number, branch, date (Pacific, 12-hour), reviewed/fix commit SHAs.
  - A 2-3 line verdict box (what is broken, what holds) + a one-line-per-finding table
    (id, severity pill, title).
  - One card per finding: severity pill + title, the figure (inlined as a data URI),
    a caption of at most 2 sentences saying what to look at, and a one-line fix
    (inline code only if the fix is a one-liner). No diff blocks unless the fix itself
    is the point.
  - If fixes were applied: test/lint results, commits, and (if restacked) what was
    ported into which upstack MR, including any merge traps caught.
  - A "where things stand" section: what is pending on the user (e.g. submit draft
    replies), deferred questions.
- **Self-review before sending**: screenshot the rendered page (playwright headless
  chromium via `pixi exec --spec python=3.12 --spec playwright-python -- python shot.py`,
  run from the scratchpad; never `uv run`, which turns the worktree into a uv project and
  leaves an untracked uv.lock + a multi-GB .venv), read the screenshots, and fix layout or
  content problems (overlaps, meaningless labels, wrong claims) before
  `astra send-doc <file> "<caption>"`.
- **Self-review every figure for content first** (resize, read it): does it show what
  the caption claims, are readouts sampled at the right place, are labels true for the
  data actually plotted (a picked ray's 2nd hit may be a wall, not the ceiling), same
  scale across compared panels, pixel size not finer than the sampling it displays.
- **Also send each figure** with `astra send-photo <png> "<id> <severity>: <one line>"`
  in the same turn, so the user can scan the findings without opening the HTML.

### 8. Persist the review state (ALWAYS, before finishing)

Write (or update) the branch review file at
`site3d_project_root/docs/branches/<branch-with-slashes-as-dashes>-review.md` so the
next `/mr-review` invocation (step 0) — or a session handling follow-up reviewer
questions — can resume without re-doing the full dive. Record:

- **Reviewed range**: MR number, merge-base SHA, HEAD SHA reviewed, date, and whether
  fixes were applied on top (with their SHAs once committed).
- **Ground truth facts** verified in step 4 (API shapes, conventions, return contracts),
  each with the source `file:line` — these are the expensive-to-rederive part.
- **Findings ledger**: every finding with severity, `file:line`, and current status
  (open / fixed-in-<sha> / wontfix-with-reason).
- **Reviewer-comment checklist**: each discussion id (short), anchor, one-line gist,
  disposition (accepted / pushed back / pending), and the draft or posted reply.
- **Verdict** as of this review.
- **Figure script + output dir** (from 4b), so the next round re-renders the same
  diagnostics on the new HEAD instead of rebuilding them.

Keep it one file per branch, updated in place (newest state wins; keep a short
"history" list of prior reviewed SHAs). This file is derived state — the MR itself
stays canonical; on conflict trust GitLab.

Also update the branch's main summary doc (`docs/branches/<branch>.md`) with a one-line
pointer to the review file if it doesn't already reference it.

### 9. Record review misses (whenever one surfaces)

When something the review should have caught surfaces later — an external reviewer's
comment, a revert, a bug found after merge, the user pointing at a figure — append one
line to `site3d_project_root/docs/branches/review-misses.md`:
`YYYY-MM-DD | MR !n | what was missed | which step should have caught it | fix made`.
Then fix it at the source, not in the chat: a rule in this skill, a seed fact in the
agent prompt, a mechanical check in step 6, or a lint rule in the repo, so the same class
of miss cannot recur. Whenever this skill is edited, re-read that ledger and check the
new text against the last few entries before saving; it is the skill's regression set.
(The two misses already baked into the prose above — the sim_range_cull margin side and
the !675 draft-note hygiene — are its first entries.)

## Notes

- Reviews are **read-only for repo code** by default. Only edit code if the user
  explicitly asks for fixes. The review-state file under `docs/branches/` (step 8) is
  always written — it lives outside the worktrees and is not version controlled.
- This skill is for reviewing a branch/MR's full diff. For reviewing just the current
  uncommitted working diff, prefer the built-in `/code-review`.
- Depth tiers (1b), the round cap (0), class-not-instance and finding confirmation with
  pre-existing tags (6, 6b), the count-first summary (7), the nit cap (posting) and the
  miss ledger (9) were adopted 2026-10-22 from the Claude Code team's code-review
  write-up (https://claude.dev/blog/solving-code-review-at-anthropic/). Its
  org-level layers (risk-tiered human approvals, CODEOWNERS, merge queue, flags,
  rollback) are team policy, not part of this skill.

## Posting review comments on the MR (when the user asks)

- Always GitLab **draft notes**, never published (see global CLAUDE.md).
- **One thread per item**, anchored on the code it is about, as a reviewer would do in the
  web UI: a multi-line `line_range` (drag-select) over the relevant lines, with line codes
  computed from the base..head diff (`<sha1(path)>_<old>_<new>`; unchanged lines outside
  hunks need the old line number). When the cause lives elsewhere, add a short comment
  anchored there too. Only truly cross-cutting notes (a short summary, a missing-tests
  list) are general comments.
- **Proposed code changes: GitLab suggestion when applicable, diff otherwise** (user
  preference 2026-10-01, supersedes the 2026-09-30 diff-only rule). Use a ` ```suggestion `
  block (renders red/green with an "Apply suggestion" button once published) whenever the
  change replaces exactly the lines the thread is anchored on: anchor the thread's
  `line_range` on precisely the replaced lines, set `position.new_line` to the range END, and
  write ` ```suggestion:-N+0 ` (N = lines above the end line) followed by the full replacement
  text. Design the anchors for it: when one fix spans separate hunks, give each hunk its own
  short thread with its own suggestion (the main thread carries the explanation, the other
  says what it pairs with). Fall back to a ` ```diff ` block (`-` current lines from HEAD, `+`
  new lines) only when a suggestion can't apply: change in another file than the anchor,
  non-contiguous lines in one thread, illustrative/pseudo code, or a published thread whose
  anchor can't be moved. Tell the user that in unpublished drafts a suggestion may show as a
  plain white block; it renders after publishing.
- **No internal review labels** (B1, S1, D1, G1, T1...) in anything posted. Those IDs are
  for the user's own review files and the HTML briefing; the team doesn't use them. Name
  the problem in words ("the crash at `max_bounces >= 2`").
- **Only what this MR needs.** No review history, no references to earlier MRs or the
  user's other work, no demo/scene names the author didn't use, no speculative follow-ups
  or out-of-scope asides. Each comment: the problem, the evidence (a figure and one or two
  numbers), and the ask.
- **Every comment carries an ask.** No no-action comments ("also passes on old code, but
  worth keeping", "same as the other thread", pure FYIs): drop them, or fold a needed
  location into the thread that asks (link the line instead of posting a pointer comment).
  If a finding implies an action, state it as a request. The summary note is the only
  exception.
- **Realistic scenarios only.** Before raising a failure found in a test geometry (radar
  inside a closed box, etc.), check it can happen in real use; drop it if not.
- **Cap the nits.** At most three style / naming / wording threads per MR, and only ones a
  human reviewer on the team would otherwise ask about; fold the rest into one line of the
  summary note or leave them in the HTML briefing. Bugs and soundness findings have no cap.
- **Pre-existing findings are not review comments** unless the MR touches those lines or
  the user asks; when one is posted, its first line says it predates this MR.
- A `draft_notes/:id` PUT with only `note` wipes the position: always resend `position`.
- The user edits drafts in the web UI while you work: re-fetch before any change, edit
  only what was asked, and never overwrite a draft the user may be editing without asking.
  (Lessons 2026-09-30, MR !675: internal labels, references to !380 and an out-of-scope
  physics aside were posted; the user had to clean them up by hand.)
