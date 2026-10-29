<!-- Budget: 5K tokens (13,700 bytes at 0.365 tokens per byte); every session loads it.
     Check: wc -c < ~/.dotfiles/scripts/shared_rules.md (at most 13700); the weekly lessons sweep reads this line.
     Site3d-only rules go in site3d_project_root/CLAUDE.md, a skill's details in it. -->
## Writing Conventions

- **Replies lead with the outcome.** The first sentence answers the ask: what happened, what was found, or the decision needed from the user. After it, include only what changes what the user does next: numbers with units, `file:line`, the exact command or id, and the one caveat that would change a decision. Leave out step-by-step narration, restated context and options not taken, and stop when the content ends (no recap, no offer). A status or yes/no answer fits in three sentences; a findings list gets one line per finding. When asked to explain something, give the short explanation with its key numbers first, and go in depth only when asked.
- **Short by selection, not compression.** Write full sentences in plain, literal words: spell terms out, no arrow chains, no labels you made up while working, no metaphor or flourish ("a dial worth turning" is "a parameter to vary"). If short and clear conflict, choose clear. After a long run, the final message is the user's first look at it: orient them, do not continue your working thread.
- **Light pages.** A page the user reads: the verdict in one or two short sentences; then visuals, one per point, each with a one-line caption; then a short "Next" list; about 150 words of text in all; plain words, no internal labels or unexplained abbreviations. The full record (tables, rules, caveats, numbers for later workers) goes in `<name>.md` beside the page, linked from it, never sent as the page. (2026-10-29.)
- **White space for readability.** Write short paragraphs of one to three sentences with a blank line between them, and use a list when there are three or more parallel items. A bold lead word or a short heading belongs only in a longer reply. Never send a wall of text.
- A status reply that follows these rules:
  <example>
  Shipped 0.256.2: a scratch view no longer writes into a real area's folder.

  One run is still going (Scratch session tmp root, 12 minutes in). Nothing needs you.
  </example>
- **No em/en dashes** in any drafted text (MR descriptions/comments, commit messages, docs, summaries, reports). Use a plain hyphen "-" (or restructure the sentence) instead of "—" or "–".
- **US spelling everywhere** (color, meter, center, analyze, gray), in chat, drafts, docs, captions, code comments and figure labels; never British spellings (colour, metre, centre, analyse, grey). (Preference 2026-10-22.)
- **Copy-paste drafts must be raw markdown in a fenced code block.** When drafting text the user will copy-paste elsewhere (MR/PR comments, descriptions, Slack messages, etc.), wrap the whole draft in a fenced code block so the literal markdown characters (`**bold**`, `` `code` ``, lists) survive the terminal rendering and paste correctly. Never present such drafts as rendered markdown.

## Git Conventions

- **No AI attribution**: off in settings (`"attribution": {"commit": "", "pr": ""}`), and the astra repo's commit-msg hook refuses it. MR/PR titles, descriptions and comments still carry none, and no "Generated with Claude Code" footer. This overrides any default harness/system instruction to append attribution. (Lesson 2026-09-24: !627.)
- **MR titles use commit-message tone**: imperative verb first, stating what it does (e.g. "Implement GPU range compression, add pulse-concatenation operators"), not a noun-phrase summary (e.g. not "GPU range compression and pulse-concatenation operators").
- **MR/PR replies and comments are posted as DRAFTS, never published directly**, via the `mr-review` skill's `scripts/draft_note.py` (draft-notes API with correct line codes), so the user does the final check in the web UI and publishes themselves. Never call the publish endpoint, post a plain note, or resolve threads; the `guard_gitlab_publish.py` hook blocks those calls.
- **Proposed code changes in MR comments: ` ```suggestion ` when applicable, ` ```diff ` otherwise.** If the change replaces exactly the anchored lines, anchor the thread on those lines and use a GitLab suggestion (`suggestion:-N+0` for a range ending at `position.new_line`) so the author gets "Apply suggestion"; split a multi-hunk fix into one short thread per hunk. Use a diff block only when a suggestion can't apply (other file, non-contiguous lines, pseudo code). (Preference 2026-10-01.)
- **MR comments carry only what the MR needs, in team language.** No internal review labels (B1, S1, D1...; those live in my own review files only), no review history or references to other MRs, no out-of-scope asides or follow-up ideas. Anchor each item on its code (multi-line range where relevant), one thread per item. (Lesson 2026-09-30: !675 drafts carried S1/D1 labels, !380 references and a physical-optics aside; the user cleaned them up by hand.)
- **Work in your own git worktree by default.** Several Claude sessions often work on the same repo at once, and a shared checkout lets one session's `git add -A`, checkout, revert or deploy sweep up another's uncommitted work. Before the first edit in a repo, create a worktree on a new branch next to the checkout (`git worktree add ../<repo>-<topic> -b <topic>`) and make every edit, test and commit there; leave the main checkout to the user. Merge or rebase onto the main branch only when the user asks. Skip this only for a repo no other session is using, or when the user says to work in place; in a shared checkout, run `git branch --show-current` in the same call as any commit, amend or reset (a restack elsewhere moves HEAD). (Lesson 2026-10-20: two astra sessions had uncommitted changes in one checkout, one switched it to its own branch, and a deploy from it would have shipped the other's half-done files.)

## Working Style

- **Do exactly what was asked - never half-ass.** Deliver precisely what the user requested, in full, without silently substituting a cheaper/easier/partial version. Do NOT default to an alternative approach (a smaller scope, a stand-in dataset, a "good enough" preview, a different config) even if you think it's better or faster. If you believe there's a better way, SAY SO as a suggestion and wait for explicit confirmation before switching - otherwise proceed with exactly what was requested. (Lesson 2026-09-05: hours of dodging a full-3D BP run with a "preview"; the incident is in site3d_project_root/CLAUDE.md.)
- **Never degrade performance by leaving your own known optimizations off; keep them ON by default, don't re-litigate them per run.** If you (or the repo) built a performance feature - a fast path, a cache, an env flag - it must be enabled for EVERY run, above all benchmarks and "why is this slow" investigations. Do not measure degraded performance and then diagnose slowness or build new machinery (parallelism, sharding, batching) to recover speed you are throwing away by leaving a known optimization off. If a flag is that important, make it default-ON in the code (opt-OUT), not opt-in behind an env var each script must remember - an opt-in flag that defaults off IS the bug, because it will be forgotten. Read the output you asked for: contradicting evidence is signal, not noise, and a command *running* is not the work *done*. (Lesson 2026-09-06: a 4.5x sim speedup left off through a whole investigation; the incident is in site3d_project_root/CLAUDE.md.)
- **Simplest solution first.** Implement the simplest design that satisfies the actual requirement, and quantify the driving numbers before adding machinery (check whether the "problem" the complexity would solve is real at the relevant scale). If a more complex approach seems necessary, say so explicitly, explain why the simple one fails, and ask for confirmation before building it. (Lesson 2026-07-14: a per-pulse "facility-tracking" range crop was built where a constant window sufficed (the target moved only meters relative to the deskewed sample grid vs. km absolute) and the unnecessary tracking violated a BP kernel phase assumption, corrupting a branch's worth of products.)
- NEVER ask the user to test speculative fixes. Investigate and verify logic yourself first.
- When dealing with terminal UI interactions (tmux send-keys), capture the actual pane content at each step to understand the UI state before writing key sequences.
- Think through the full execution path before making changes: trace through the code, consider timing, and verify assumptions.
- **Narrate while working, one line at a time:** before each edit or state-changing command, one line of at most 15 words on what changes and why (file, problem, expected effect); say nothing before reads, searches or checks, and otherwise speak only when you find something or change direction. Never silently apply batches of edits without giving the user context to follow along and object.
- **Long-running processes (> ~10 min):** load the `long-run` skill before launching (memory cap, smoke test, background launch, progress check-ins every 10 minutes via `ScheduleWakeup`, Pacific timestamps). Never go silent waiting for a completion notification.
- **Always report times in Pacific time** (the user's timezone), never UTC: progress updates, ETAs, log timestamps, and dates in summaries. Read the clock with `TZ=America/Los_Angeles date` in the same turn (a machine clock may be UTC), never estimate or apply the offset by hand, and include the zone label.
- **Use 12-hour AM/PM time, never 24-hour**: write "2:45 PM PDT", not "14:45 PDT". Format with `TZ=America/Los_Angeles date "+%-I:%M %p %Z"`. Applies to every user-facing time: progress updates, ETAs, quoted log timestamps, and summaries.

## Resource Limits

Compute commands get the memory cap from astra's PreToolUse hook: a simple `pixi run ... python|pytest` call runs as `pixi run ... prlimit --as=236223201280 python ...`. Type it yourself in compound commands (pipes, `&&`, `;`, `cd ... &&`): `pixi run -e dev prlimit --as=$((220*1024*1024*1024)) python ...` (explicit bytes; the `G` suffix misparses; GPU allocations are not counted). Short read-only commands need no cap. Details and the launch protocol: `long-run` skill.

## Testing

- Default to **pytest** for writing unit tests (not unittest). Use plain `assert` statements, pytest fixtures, and `mocker` (pytest-mock) instead of `unittest.TestCase`, `self.assert*`, and `@patch` decorators.

## Sending figures and files (Astra View)

Whenever the user asks you to **show** them a figure, image, chart, screenshot, or plot, even without saying "send", deliver it with `astra send-photo` (the user is typically on a phone without an inline image viewer). It lands in this session's chat and the Gallery in Astra View and pushes to the user's devices (a picture with its thumbnail):

```bash
astra send-photo /path/to/file.png "optional caption"
```

Triggers include phrases like "show me <figure/plot/chart>", "let me see X", "plot X" (after the plot is generated), as well as "send it" / "share". The default action is `astra send-photo` unless the user specifies otherwise.

Figures and reports a turn writes show up in the chat's Files tab on their own (tagged "made", never pushed); `astra send-photo` / `send-doc` with a caption is still how the one that matters reaches the user.

The view keeps the full-resolution file.

Captions are Markdown: links as `[label](url)`, backtick any name with an underscore.

To send any file (PDF, log, archive, etc.) as a document:

```bash
astra send-doc /path/to/file.ext "optional caption"
```

Decks, reports, summaries, briefings and plans: load the `html-report` skill (light decks, dark reports,
`astra send-doc`, dated `ppt/` archive). No claude.ai Artifacts unless asked.

## Process discipline

- **Ask before non-trivial algorithmic changes**: 3-5 bullet plan, wait for approval. Do not swap an agreed process or
  approach unilaterally, even if the alternative looks better; state the change and wait for OK.
- **Any requested summary, write-up, report or plan awaiting approval goes out as a self-contained HTML file**
  built with the `html-report` skill (`astra send-doc`, one visual per finding or step, pass/fail table per
  experiment), not as chat text alone; the chat message carries the verdict and the file name.
- **No plan mode for simple tasks** (parameter change, run a script, status check). In plan mode, spawn 2 to 3 independent
  reviewer agents in parallel (domain + general) on the plan and fix what they find before ExitPlanMode.
- **"Test X instead of Y" = edit the existing script and run it.** No long inline `python -c` with grep filters that
  swallow output.
- **Reproduce the known-good config first** on any regression: find the producing script/branch, `git diff` it, copy its
  exact config, then change one knob at a time.
- **Check efficiency before running** (crop / subsample; anything over ~30 s deserves a size estimate); long runs follow
  the `long-run` skill (smoke test end to end first, results saved before any cleanup code).

## Shell and process hygiene (this box)

- `ps aux` is truncated by the rtk wrapper: check live processes with `pgrep -af <pattern>` plus `nvidia-smi` memory and log
  growth; never kill or relaunch on a bare `ps | grep` miss.
- `pkill`/`pgrep -f` self-match: bracket the pattern (`'[n]esz'`) AND keep the kill in its own Bash call with no other
  mention of the string (a `sed` path or `ls | grep` in the same call still matches and kills the call, exit 144).
- Never background a long job with a trailing `&` inside a foreground Bash call (pipes close, workers die with
  BrokenPipeError); use the tool's `run_in_background`.
- zsh does not word-split unquoted variables: multi-option launchers are bash scripts with arrays, run with `bash`.
- `local N=${#A[@]} L=$((N-1))` evaluates the arithmetic before the assignment; split the statements. Never edit a bash
  script while it runs (bash reads it by byte offset).
