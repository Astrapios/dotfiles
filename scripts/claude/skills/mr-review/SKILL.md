---
name: mr-review
description: Review and summarize a merge request (MR/PR) for the site3d repo - coding style (repo conformance), soundness, and logic. Verifies data-flow contracts against the codebase, then fans out domain-expert reviewers (SAR physics, computer vision, etc.) seeded with verified ground truth, and delivers a figure-per-finding HTML briefing. Use when the user says "review this MR", "/mr-review", "what does this branch change", "summarize the MR", asks to critique a branch's changes, or wants review replies drafted on GitLab.
allowed-tools: Read, Bash, Grep, Glob, Agent, Write, Edit
---

# MR / Branch Review

Summarize and review the changes on the current branch (or a named one) against `main`:
**coding style (repo conformance), soundness, and logic**. Fan out domain-expert reviewers for
the parts of the diff that touch their domain.

Guiding principle: **establish ground truth before delegating.** A reviewer agent that
re-derives API shapes and coordinate conventions from scratch gets them wrong and wastes its
budget. Verify the contracts yourself, then hand the agents verified facts so they spend their
effort on judgment, not archaeology.

Files in this skill (one level deep, read when the step says so):
- `references/posting-glab.md` - posting draft notes on the MR (rules, suggestion syntax, scripts)
- `references/html-briefing.md` - the HTML briefing: structure, figures, self-review, sending
- `scripts/mr_discussions.py`, `scripts/draft_note.py` - run them, do not hand-roll glab calls
- `evals/` - regression prompts built from past misses

## Steps

### 0. Load prior review state (incremental re-review)

Reviews persist per branch (step 8) at
`site3d_project_root/docs/branches/<branch-with-slashes-as-dashes>-review.md`.
If it exists, read it and review **incrementally**:
- Diff only what changed since the recorded SHA plus files the new commits touch.
- Re-pull the MR discussions and process only comments that are new or newly unresolved;
  carry forward prior dispositions.
- Reuse the recorded ground truth; re-verify a fact only if the new diff touches its module.
- Scope the domain agents (step 5) to the delta; no full fan-out unless the user asks.
- **Round cap**: after the third fix-and-rereview round on the same MR, stop iterating on
  findings and say so. A change that has not converged in three rounds has an approach
  problem, not a bug list; name it and ask the user to reconsider it.

### 1. Scope the diff and pick the depth

- `git branch --show-current`, `git log --oneline -8`, `git merge-base HEAD main`; the MR is
  merge-base..HEAD. Stacked branches: `gs ls` FIRST, review only this branch's commits.
- `git diff --stat <merge-base>..HEAD` for the file list. **Include `experimental/` and
  `test_scripts/`**; the user's sims and per-user pipeline code live there.
- **Depth tier**, chosen by what the diff touches, never by who wrote it; state it in the report:
  - **Quick** - docs, comments, test-only or config-only changes, renames, additive tooling
    under `experimental/` or `test_scripts/` with no production import. Steps 1-3, step 6
    (ruff + dead-parameter sweep), short chat summary, step 8. No fan-out, no refuter, no
    figures, no HTML unless a real bug turns up. Do not pad a clean Quick review with nits.
  - **Standard** - ordinary production changes. Full flow, domain agents only for the domains
    actually touched.
  - **Deep** - kernels, geometry and coordinate conventions, phase-sensitive products (BP, PFA,
    sim kernels, InSAR, DOF), range gating and pulse timing, anything the MR description calls
    "exact", "conservative" or "no-op", and any diff over ~1000 lines. Full flow plus the
    refuter and the brute-force checks of step 4b for every claim.
  A Quick MR that turns out to touch a Deep path is promoted on the spot; say so.

### 2. Pull the MR and reviewer comments

If an MR exists for the branch: `glab mr view <iid>` for title and description (the author's
claimed contract; verify the diff against it), then
`python scripts/mr_discussions.py [iid]` for the authoritative thread list with anchors and
resolved state (`--all` for resolved ones). Build a checklist of **open** comments and seed the
relevant ones into the domain agents (step 5). Resolved threads are context, not action items.

### 3. Read the full diff

Read every changed file in full, not just the hunks; context around a change is where bugs
hide. For a new file, read the whole thing.

### 4. Establish ground truth (BEFORE launching agents)

For each external symbol the diff depends on, open the source and confirm: dataclass field
names, shapes, dtypes, `[row,col]` vs `[x,y]`; normalization and units (linear vs dB, [0,1] vs
raw); whether a docstring matches the code that produces the data (**trust the code**, note
mismatches); repo conventions from CLAUDE.md (no `__init__.py`, full-path imports,
`jsonargparse.CLI` + `autoinit_logger()` for scripts, logging not print, Google docstrings,
ruff line length 100, `dev` pixi env). Write the confirmed facts down; they seed the agents.

**Separate FACTS from CLAIMS.** A fact is read directly from code. A claim is anything
DERIVED: a margin argument, a convexity or sign property, "this bound is conservative", "this
error is summation order", "this op is a no-op". Facts seed the agents as trusted ground
truth; claims go to 4b and to the refuter in step 5, labeled as targets to attack. Never let
your own analysis ride into agent prompts disguised as fact.

### 4b. Adversarially verify every safety or bound claim (numerically, never by prose)

Run a REFUTER, not a prover. For every "conservative", "never clips", "always safe", "exact",
"no-op", "negligible" (in the diff, the MR description, or your own step-4 analysis):
- **Brute-force counterexample search**: a small numpy script in the scratchpad that sweeps
  the parameter space and checks the claim against ground truth computed the slow obvious way
  (every pulse, every block). Verified only when the search finds no violation AND a
  deliberately weakened version of the code DOES violate; a search that cannot fail proves
  nothing.
- **Analyze the code's literal expression**, not a physical proxy: convexity, monotonicity and
  sign for the exact quantity the code computes (`(tau - sample_t_start[p]) * fs`, where the
  window also moves per pulse). Differences of convex functions are not convex.
- **Asymmetry smell**: any pad, margin, clamp or tolerance applied to ONE side of a symmetric
  problem gets a numeric answer to "why not the other side, and which side does the failure
  mode attack?"
- **Residual-error budget**: when a measured error is attributed to a benign cause, bound what
  that cause CAN produce at the dtype and operand count. An error orders of magnitude above the
  bound is an unexplained mechanism; chase it.
- **Constructive alternative**: for each approximation, one explicit thought on "is there a
  cheap exact alternative?" (a log-N bisection vs a sampled envelope). If small, prototype or
  propose it; if its weakness can be neutralized cheaply (UNION with the existing bound), say so.
- **Every check renders its diagnostic figure** (see `references/html-briefing.md`): one script
  `site3d_project_root/test_scripts/<branch>_review_figs.py`, output `outputs/<branch>_review/`.

### 5. Fan out domain-expert reviewers (parallel, single message)

Pick by what the diff touches; launch concurrently in ONE message:
- **SAR / radar physics** -> `sar-physics-reviewer` agent: phase history, image formation,
  motion comp, range compression, coordinate transforms, phenomenology, geolocation conventions.
- **Computer vision / image processing** -> `general-purpose` framed as a CV expert.
- **Other domains** (GPU/cupy, AWS workflow, data pipeline) -> `general-purpose` framed
  appropriately; `Explore` for pure "where is X used" questions.
- **Claim refuter (ALWAYS when step 4 produced claims)** -> one `general-purpose` agent whose
  ONLY job is to attack: hand it the fact list, the claim list (labeled as under attack), the
  key hunks; concrete counterexamples, weakest claim first; a null result is reported as
  "attacked X via Y, held", never as praise. It is the only reviewer not marinated in your
  framing when you reviewed code you helped write.

In each prompt: absolute file paths, read fully; seed the verified FACTS and say not to
re-investigate them; pass claims only marked "unverified claim"; scope to its domain only;
line-referenced findings with a severity (OK / minor / significant / bug), bugs distinguished
from nits; **no file edits**. Calibrate severity: `experimental/` and one-off tools get
latitude, but real physics and logic errors still surface.

### 6. Mechanical checks

- `pixi run -e lint ruff check <changed files>`.
- Tests: note whether they exist and would run; verify logic yourself, never ask the user to
  run speculative things.
- **Dead-parameter sweep**: every parameter of every touched signature is still READ in the
  new body (a culled loop that stops reading `n_targets` leaves a live arg feeding a dead param).
- **Load-bearing-op sweep**: for each defensive op near the hunks (clip, clamp, guard, re-sort,
  cast), ask whether the API contract already guarantees it. Dead guards mislead readers.
- **Test blind-spot audit**: for each new or changed test, which failure modes CANNOT fail it
  BY CONSTRUCTION (a 3-pulse geometry makes envelope sampling exhaustive). Each blind spot is
  covered by another test or reported with a concrete test recipe.
- **Fresh-eyes nit pass**: unexplained naming conventions, unqualified performance numbers in
  comments, locale spellings, stale references to removed code.
- **Re-litigate wontfixes**: a prior "kept deliberately" is not settled; re-test it against repo
  convention and state why it survives or falls this round.
- **Flag the class, not the instance**: when a finding is a pattern, grep the whole diff for
  every occurrence and report ONE finding listing all locations.

### 6b. Confirm every finding before it is reported

Agent output is a lead, not a finding.
- **Reproduce it yourself** against the code: open the `file:line`, trace the failing input to
  the wrong output, and write the failure scenario (inputs and state -> wrong result) in one
  sentence. A finding whose scenario you cannot construct is dropped or, at most, listed as
  "unconfirmed" at the end. When two agents disagree, the code decides.
- **Tag pre-existing problems**: check the merge-base version (`git show <merge-base>:<path>`,
  `git blame`). A bug the MR did not introduce is tagged **pre-existing**, listed separately,
  never blamed on the author or raised as a blocker, posted only when the MR touches the lines
  or the user asks. Reading whole files surfaces old code constantly; this tag is what keeps
  review rounds converging.
- **Drop cleanup-only output**: pure style, diagnostics or refactoring preference with no
  failure scenario goes to the style section or the nit list, never the soundness table.

### 7. Synthesize the review

A concise, skimmable report (never the agents' raw output). The chat summary is one line per
finding (severity, what breaks, fix); the detail lives in the figures, sent in the same turn.
- **Summary** - what `main` gains, 2-4 sentences, the code not the refactoring journey.
- **Soundness & logic** - open with the count ("2 bugs, 1 significant, 3 minor; 2 pre-existing
  kept separate"), then findings de-duplicated across agents, most important first, each with
  severity, `file:line` and its one-sentence failure scenario. Pre-existing in their own list.
- **Style / repo conformance** - ruff, conventions, docstring and comment issues, naming.
- **Reviewer comments** - for each open thread: is the reviewer right, is it already addressed,
  recommended response (fix / push back with reason / acknowledge). Flag open comments the
  diff silently ignores.
- **Verdict** - a short bottom line. Direct about real problems, no praise padding; if there
  are no bugs, say so. Note doc/comment mismatches (a stale module docstring) even when the
  code is correct.
- **HTML briefing** for Standard and Deep (and a Quick that found a bug): follow
  `references/html-briefing.md`, send with `astra send-doc`, figures with `astra send-photo`.

### 8. Persist the review state (ALWAYS, before finishing)

Write or update `docs/branches/<branch-with-slashes-as-dashes>-review.md`: reviewed range (MR,
merge-base, HEAD, date, tier, fix SHAs); ground-truth facts with `file:line`; findings ledger
(severity, `file:line`, status open / fixed-in-<sha> / wontfix-with-reason, pre-existing tag);
reviewer-comment checklist (discussion id, anchor, gist, disposition, draft or posted reply);
verdict; figure script and output dir. One file per branch, updated in place with a short
history of prior SHAs. Derived state: on conflict, GitLab is canonical. Add a pointer in
`docs/branches/<branch>.md` if missing.

### 9. Record review misses (whenever one surfaces)

When something the review should have caught surfaces later (an external reviewer's comment,
a revert, a bug after merge, the user pointing at a figure), append one line to
`site3d_project_root/docs/branches/review-misses.md`:
`YYYY-MM-DD | MR !n | what was missed | which step should have caught it | fix made`.
Then fix it at the source: a rule here, a seed fact in an agent prompt, a check in step 6, a
script gotcha, or a lint rule in the repo. Before editing this skill, re-read that ledger and
check the new text against it; with `evals/` it is the skill's regression set.

## Gotchas (from past misses; the ledger has the dates)

- A margin-side claim ("the pad is conservative") survived two review rounds as prose with the
  exposed side exactly backwards; a 30-line brute-force script found it in 15 minutes. Prose
  derivations of bounds are claims, not facts (step 4b).
- 7e-5 of error booked as "fp32 summation order" was partly real echo clipping; the bound for
  summation order was orders of magnitude smaller. Budget the benign cause before accepting it.
- Posted drafts once carried internal labels (S1/D1), references to another MR and an
  out-of-scope physics aside; the user cleaned them by hand. Team language, this MR only.
- A briefing made of diff blocks plus "Why:" paragraphs was sent back as too dense. Figures
  per finding, captions of two sentences.
- `glab mr view --comments` silently drops comments and anchors; only the discussions API
  counts, and it is paginated.
- A draft-note PUT with only `note` wipes the position. `draft_note.py --update` resends it.
- `glab api -f "position[new_line]=3"` sends a flat JSON key, which GitLab ignores: the draft
  lands with NO anchor and no error. Positions go as a nested JSON body (`--input` plus
  `Content-Type: application/json`), which `draft_note.py` does; it also refuses to leave an
  unanchored draft behind.
- Threads GitLab re-traced after a push keep stale line codes, so existing threads are not an
  oracle for line-code computation; GitLab's own raw diff is, and it rejects bad codes.
- `uv run` inside a worktree turns it into a uv project (uv.lock plus a multi-GB .venv); use
  `pixi exec --spec ...` for throwaway environments.

## Notes

- Reviews are **read-only for repo code** by default; edit code only when the user asks for
  fixes. The review-state file is always written (outside the worktrees, not versioned).
- For the current uncommitted working diff, prefer the built-in `/code-review`.
- Posting anything on the MR: read `references/posting-glab.md` first. Drafts only; the global
  hook blocks publish, plain notes and thread resolution.
- Depth tiers, the round cap, class-not-instance, finding confirmation with pre-existing tags,
  the count-first summary, the nit cap and the miss ledger follow the Claude Code team's
  code-review write-up (https://claude.dev/blog/solving-code-review-at-anthropic/); its
  org-level layers (risk-tiered approvals, merge queue, flags, rollback) are team policy.
