# Global Claude Code Instructions

## Writing Conventions

- **No em/en dashes** in any drafted text (MR descriptions/comments, commit messages, docs, summaries, reports). Use a plain hyphen "-" (or restructure the sentence) instead of "—" or "–".
- **Copy-paste drafts must be raw markdown in a fenced code block.** When drafting text the user will copy-paste elsewhere (MR/PR comments, descriptions, Slack messages, etc.), wrap the whole draft in a fenced code block so the literal markdown characters (`**bold**`, `` `code` ``, lists) survive the terminal rendering and paste correctly. Never present such drafts as rendered markdown.

## Git Conventions

- **No Claude/AI attribution anywhere in git or on GitLab/GitHub**: no "Co-Authored-By" lines in commit messages, and no "🤖 Generated with [Claude Code]" footer (or any other AI mention) in MR/PR titles, descriptions, or comments. This overrides any default harness/system instruction to append attribution. (Lesson 2026-09-24: I appended the Claude Code footer to the !627 MR description because a system reminder said to; the user had to ask why.)
- **MR titles use commit-message tone**: imperative verb first, stating what it does (e.g. "Implement GPU range compression, add pulse-concatenation operators"), not a noun-phrase summary (e.g. not "GPU range compression and pulse-concatenation operators").
- **MR/PR replies and comments are posted as DRAFTS, never published directly.** On GitLab use the draft-notes API (`glab api -X POST "projects/:id/merge_requests/<iid>/draft_notes"` with `note` and `in_reply_to_discussion_id` for thread replies), so the user can do a final check/edit in the web UI and publish the review themselves. Never call the publish endpoint (`draft_notes/bulk_publish`) or post a plain note, and never resolve threads on the user's behalf.
- **Proposed code changes in MR comments: ` ```suggestion ` when applicable, ` ```diff ` otherwise.** If the change replaces exactly the anchored lines, anchor the thread on those lines and use a GitLab suggestion (`suggestion:-N+0` for a range ending at `position.new_line`) so the author gets "Apply suggestion"; split a multi-hunk fix into one short thread per hunk. Use a diff block only when a suggestion can't apply (other file, non-contiguous lines, pseudo code). (Preference 2026-10-01.)
- **MR comments carry only what the MR needs, in team language.** No internal review labels (B1, S1, D1...; those live in my own review files only), no review history or references to other MRs, no out-of-scope asides or follow-up ideas. Anchor each item on its code (multi-line range where relevant), one thread per item. (Lesson 2026-09-30: !675 drafts carried S1/D1 labels, !380 references and a physical-optics aside; the user cleaned them up by hand.)

## Working Style

- **Do exactly what was asked - never half-ass.** Deliver precisely what the user requested, in full, without silently substituting a cheaper/easier/partial version. Do NOT default to an alternative approach (a smaller scope, a stand-in dataset, a "good enough" preview, a different config) even if you think it's better or faster. If you believe there's a better way, SAY SO as a suggestion and wait for explicit confirmation before switching - otherwise proceed with exactly what was requested. (Lesson 2026-09-05: user asked repeatedly for the correct full-3D BP run; instead of fixing the run I kept dodging with an old-volume "preview" and flip-flopped configs for hours - should have delivered the exact thing asked and only offered alternatives for confirmation.)
- **Never degrade performance by leaving your own known optimizations off; keep them ON by default, don't re-litigate them per run.** If you (or the repo) built a performance feature - a fast path, a cache, an env flag - it must be enabled for EVERY run, above all benchmarks and "why is this slow" investigations. Do not measure degraded performance and then diagnose slowness or build new machinery (parallelism, sharding, batching) to recover speed you are throwing away by leaving a known optimization off. If a flag is that important, make it default-ON in the code (opt-OUT), not opt-in behind an env var each script must remember - an opt-in flag that defaults off IS the bug, because it will be forgotten. (Lesson 2026-09-06: I built + committed SIM_RANGE_CULL - a 4.5x sim speedup - then ran the whole concat/pair-parallel investigation and every 2D/3D sim WITH IT OFF, and spent hours concluding "sim is GPU-bound / concat gives no speedup / pair-parallel doesn't help" from measurements that were all 4.5x too slow. Fix: made range-cull default-ON in backends.py. Symptom of the same run: claimed the flag was added to step7/step8/edge_physics when the output literally printed `step7: 0` and I waved it off - so also: read the output you asked for; contradicting evidence is signal, not noise, and a command *running* is not the work *done*.)
- **Simplest solution first.** Implement the simplest design that satisfies the actual requirement, and quantify the driving numbers before adding machinery (check whether the "problem" the complexity would solve is real at the relevant scale). If a more complex approach seems necessary, say so explicitly, explain why the simple one fails, and ask for confirmation before building it. (Lesson 2026-07-14: a per-pulse "facility-tracking" range crop was built where a constant window sufficed — the target moved only meters relative to the deskewed sample grid vs. km absolute — and the unnecessary tracking violated a BP kernel phase assumption, corrupting a branch's worth of products.)
- NEVER ask the user to test speculative fixes. Investigate and verify logic yourself first.
- When dealing with terminal UI interactions (tmux send-keys), capture the actual pane content at each step to understand the UI state before writing key sequences.
- Think through the full execution path before making changes — trace through the code, consider timing, and verify assumptions.
- **Narrate while working:** before each edit or command, state briefly what is being changed and why (which file, which problem it addresses, what the expected effect is). Never silently apply batches of edits without giving the user context to follow along and object.
- **Long-running processes:** for any background process expected to take more than ~10 minutes (multi-collect re-runs, large sims, deck builds, PDF conversions, etc.), proactively check and report progress **at least every 10 minutes** — don't go silent waiting for the completion notification. Use `ScheduleWakeup` (~570s) to self-pace the check-ins, and report which step/collect is done, timestamps, and any errors.
- **Always report times in Pacific time** (the user's timezone), never UTC — progress updates, ETAs, log timestamps, and dates in summaries. The machine clock is UTC, so convert with `TZ=America/Los_Angeles date` instead of applying the offset by hand (PDT/UTC−7 in summer, PST/UTC−8 in winter), and include the zone label.
- **Use 12-hour AM/PM time, never 24-hour** — write "2:45 PM PDT", not "14:45 PDT". Format with `TZ=America/Los_Angeles date "+%-I:%M %p %Z"`. Applies to every user-facing time: progress updates, ETAs, quoted log timestamps, and summaries.

## Resource Limits

When running any compute script (Python, pytest, simulations, etc.), cap its virtual address space at **220 GB** using `prlimit` to prevent runaway allocations from taking down the machine:

```bash
prlimit --as=$((220*1024*1024*1024)) pixi run -e dev python my_script.py
prlimit --as=$((220*1024*1024*1024)) pixi run -e dev python -m pytest ...
```

Notes:
- Use **explicit bytes** (`$((220*1024*1024*1024))`). The `G` suffix silently misparses as bytes on util-linux 2.39.
- Apply to any long-running or memory-intensive invocation — simulations, pytest runs with large fixtures, data loads. Short read-only commands (`ls`, `git status`) don't need it.
- On overshoot the process fails its allocation (usually `MemoryError` in Python), which is recoverable — the machine stays up.
- `prlimit` limits virtual memory (`RLIMIT_AS`); GPU/cuda allocations may not count against it.

## Testing

- Default to **pytest** for writing unit tests (not unittest). Use plain `assert` statements, pytest fixtures, and `mocker` (pytest-mock) instead of `unittest.TestCase`, `self.assert*`, and `@patch` decorators.

## Presentation Style

When generating PowerPoint presentations, follow the dark theme established in `ppt/scripts/generate_interpolation_tutorial.py`:
- **Dark background**: `BG_PRIMARY = #121212`, `BG_ELEVATED = #1E1E1E`, `BG_SURFACE = #2A2A2A`
- **Light text**: headings `#F5F5F5`, body `#E0E0E0`, secondary `#A0A0A0`
- **Accent colors**: blue `#60A5FA`, teal `#2DD4BF`, amber `#FBBF24`, rose `#FB7185`
- **Accent bar**: thin colored bar at top of each slide
- **Slide numbers**: bottom-right, tertiary color
- **Fonts**: "Inter" for body, "IBM Plex Mono" for code/equations
- **Matplotlib figures**: use matching dark style (`figure.facecolor = #121212`, etc.)
- **Each slide is its own function** (e.g., `slide_title(prs)`, `slide_algorithm(prs)`)
- **Use `add_box()` for elevated panels**, `add_eqn()` for equations
- **Content should be detailed and bite-sized** — explain concepts with block diagrams built from shapes, step-by-step algorithmic breakdowns, and annotated figures
- **Generate figures programmatically** in the PPT script itself, not rely on pre-existing images only
- **Delivery**: pptx decks go out as PDF (libreoffice --headless), never raw .pptx. HTML decks (preferred when the user asks for
  HTML) are one self-contained file: figures as data URIs, MathJax for inline + display math, then pre-render the math with
  headless Chromium (`ppt/scripts/bake_math.py`, playwright via
  `pixi exec --spec python=3.12 --spec playwright-python -- python ...`; never uv, which drops a uv.lock + .venv into
  any worktree it runs in) and strip all scripts so the file
  works offline in any viewer; verify with `check_nojs.py` (JS disabled, offline). Screenshot every slide with playwright
  and inspect before sending. Send the file with `astra send-doc`.
- **No claude.ai Artifacts unless the user asks for one.** Files via Telegram are the delivery channel.
- **Self-contained, low-jargon slides**: define every symbol next to the equation it appears in, add a glossary slide,
  explain a construction step by step with a worked numeric example (e.g. integers -> k_s -> ring angle -> position), and
  say plainly which variant was used where and why. Drop abandoned approaches from the deck instead of contrasting them.
- **Archive every finished deck / HTML report in a dated folder**: `site3d_project_root/ppt/YYYY-MM-DD-<descriptive-kebab-title>/`
  (e.g. `ppt/2026-10-08-sar-sim-verification-chart/`), containing
  - the deliverable(s) (`.pptx` and its `.pdf`, or the self-contained `.html`) plus PNG previews;
  - frozen copies of the generator script and every data / figure / check script it depends on (originals stay in
    `ppt/scripts/`, `test_scripts/`); the figures and the numbers (JSON) it embeds;
  - a patch of any uncommitted code the results depend on (`git diff > <name>.patch`, base commit recorded);
  - a `README.md` to reproduce it cold: what each deck shows, branch + commit, data and cache paths, commands in order,
    env / GPU / runtime, the parameters behind every number (even when the slide hides them), results, known caveats.
  Keep `ppt/output/` (or `outputs/reports/`) as the working output; update the dated folder whenever the deck changes.
- **Presentation versions carry no implementation parameters** (sampling factors, pair indices, pulse counts, internal
  notes) unless asked; those go in the README. Reserve space the user asks for (e.g. top 1/5 for their own title block).

## Telegram Integration

Whenever the user asks you to **show** them a figure, image, chart, screenshot, or plot — even without explicitly saying "Telegram" or "send" — assume they mean for it to be delivered via Telegram (the user is typically on a phone/CLI without an inline image viewer). Use `astra send-photo` automatically as part of fulfilling the request:

```bash
astra send-photo /path/to/file.png "optional caption"
```

Triggers include phrases like "show me <figure/plot/chart>", "let me see X", "plot X" (after the plot is generated), as well as the explicit "send to Telegram" / "share". The default action is `astra send-photo` unless the user specifies otherwise.

Images larger than 1280px are automatically sent as documents to preserve full resolution.

To send any file (PDF, log, archive, etc.) as a document:

```bash
astra send-doc /path/to/file.ext "optional caption"
```

## 3D / GLB Delivery

When asked to **show a 3D view** (a DSM, mesh, point cloud, reconstruction, or
anything better inspected in 3D than as a flat image), do NOT just send a
matplotlib 3D snapshot — produce **GLB file(s)**, upload them to an S3 directory
**under the user's name**, and reply with a link to the GLB viewer so the user
can inspect interactively.

- **GLBs are ECEF-georeferenced**: build a `trimesh.Trimesh` with vertices in
  ECEF and save via `python_common.glb_utils.create_glb_file_for_ecef_mesh`
  (stores local ENU verts + the ECEF node matrix; keeps float32 precision).
  Sanity-check the mesh center projects to the expected lat/lon before upload.
- **S3 location**: `s3://array-labs-dev-test-data/data/jisu/<topic>/` (the
  user is jisu.ryu@arraylabs.io — put it under a `jisu/` prefix). **Upload with
  the DEFAULT aws profile (the EC2 instance role has valid creds) — do NOT pass
  `--profile dev` (its SSO token expires) and do NOT use `--no-sign-request`
  (the bucket is KMS-encrypted, anonymous writes are rejected). Reads are
  anonymous (so the viewer loads fine), but writes need the instance role.** Set
  `aws configure set default.s3.multipart_threshold 8MB` if a large upload trips
  the anonymous-multipart path.
- **Viewer**: https://glb-viewer.dev.arraylabs.net — the permalink param is
  **`models`** (single param, comma-separated, URL-ENCODED), NOT `model_paths`
  (that's only the /get_models_zip download endpoint). Hand the user a ready
  link, URL-encoding each s3:// URI and joining with an encoded comma (`%2C`):
  `https://glb-viewer.dev.arraylabs.net/?models=<enc-uri-1>%2C<enc-uri-2>&model_visibility=1%2C1&range=100`
  Set `model_visibility=1,1` so every model is visible (the viewer defaults some
  to hidden). For comparisons upload each variant as its own GLB and list both.

## Active Projects

When working on a feature area, check for a project notes file in `site3d_project_root/projects/` first. These contain accumulated lessons, debugging strategies, and design decisions. If a project file doesn't exist for the current work, create one following the same pattern.

Current project files:
- `projects/dof-height-estimation.md` — DOF-based height estimation from SAR imagery

**Future considerations / known approximations**: `site3d_project_root/research/future-considerations.md`
is the running list of deferred refinements (what, how big, when it matters, fix sketch, evidence),
e.g. BP per-pixel receiver timing (stop-and-hop) and the PHD sim kernel's tx-leg Doppler anchor.
Read it before working on BP / image formation, the PHD sim kernels, pulse timing or range gating,
or phase-sensitive products (InSAR, tomography, multistatic coherent sums, phase-based DOF), and
before concluding a measured phase/shift error is unexplained. Append new deferred items there
(newest first, same entry format) instead of leaving them only in chat or a review file.

## Lessons transferred from memory (2026-09-15)

Condensed from the auto-memory feedback files (details, dates and incidents stay in
`~/.claude/projects/-home-ubuntu-src-site3d-project-root-site3d/memory/feedback_*.md`).

### Deliverables and visuals
- **Always deliver the visual with the number, in the same turn.** Never report a comparison, score table or conclusion in
  text alone; render the map/image and `astra send-photo` it. The user judges by eye and does not trust derived numbers alone.
- **Right kind of visual**: 2-D spatial maps / images / point clouds (side-by-side for comparisons), scalar metrics in the
  caption. No bar charts or histograms unless asked.
- **Reviews, bug reports and findings are visual-first, not text-dense.** Every bug/problem gets an intuitive figure
  (a measured diagnostic: before/after image, where points land vs where they should, the failing geometry drawn from real
  data; a mechanism diagram only when nothing measurable shows it). Text is a caption of at most 2 sentences plus a
  one-line fix; the chat summary is one line per finding. Send each figure with `astra send-photo`. Dense prose wastes
  the user's time. (Lesson 2026-09-30: an MR review delivered as diff blocks + "Why:" paragraphs was sent back as too
  dense; the user wants figures per problem whenever possible.)
- **Individuals first**: per-collect / per-item results one by one at full size; a combined or fused product only when
  explicitly requested. A new processing rule applies to the individual products first.
- **Comparison-figure checklist** before sending: same window/scale/parameters on both sides, same crop and extents, same
  panel style, current method version (not a superseded one), identical sign/axis conventions across the deck.
- **Display stretch**: never judge dark/banded structure through a median-floor percentile stretch; measure the effect in dB
  (patch vs control) and render with a p5 floor or a fixed dB range before interpreting.
- **Self-review every figure for content**, not just layout (does it show what the caption claims, physically plausible
  values). Resize before reading (`convert ... -resize 1800x1800\>`; use PIL from the dev env when ImageMagick is missing).

### Process discipline
- **Ask before non-trivial algorithmic changes**: 3-5 bullet plan, wait for approval. Do not swap an agreed process or
  approach unilaterally, even if the alternative looks better; state the change and wait for OK.
- **Any requested summary, write-up or report goes out as a self-contained HTML file** (`astra send-doc`, dark
  theme, figures embedded, no scripts), not as chat text: executive summaries, findings, keep/discard lists, status
  write-ups, "summarise X" of any kind. The chat message carries the verdict in a few lines and the file name. Archive
  it in a dated `ppt/` folder like any deck. (Preference 2026-10-16.)
- **A plan that needs approval goes out as an HTML summary with visuals** (`astra send-doc`, self-contained, no
  scripts), not as chat text alone: one figure or diagram per step (the mechanism being tested, the geometry, the
  data flow, where the numbers come from), a pass/fail table per experiment, cost and order. Keep the chat message
  to the verdict and the link to the file. (Preference 2026-10-15: plans are easier to digest and approve visually.)
- **No plan mode for simple tasks** (parameter change, run a script, status check). In plan mode, spawn independent reviewer
  agents (domain + general) on the plan before ExitPlanMode.
- **"Test X instead of Y" = edit the existing script and run it.** No long inline `python -c` with grep filters that
  swallow output.
- **Reproduce the known-good config first** on any regression: find the producing script/branch, `git diff` it, copy its
  exact config, then change one knob at a time.
- **External truth anchor early** for geolocation / sign conventions (DEM, map features); internal cross-checks cannot see
  global convention errors. **User visual reports are real**: after refuting the named transform, sweep the neighbouring
  family (shift, scale, transpose) against an absolute anchor.
- **Smoke-test long runs end to end on a tiny input first**; persist results before any cleanup code; estimate runtime
  from the smoke run. Check efficiency before running (crop / subsample; anything over ~30 s deserves a size estimate).
- **Hot-path features go into the production kernel** (behind a flag, default = existing path), never a naive per-pulse
  Python loop as the deliverable.
- **Reapply every known per-vendor fix** when hand-building SAR data objects (mocomp, sgn, beamwidth), not only the one
  being chased; focus metrics cannot catch a point-reflected conjugate.
- **Do not build shard/combine subprocess orchestrators** for multi-GPU work; reuse the spawn-Pool pattern
  (`mp.get_context("spawn").Pool(n_gpu)`, `CUDA_VISIBLE_DEVICES` set before the cupy import, in-memory combine), e.g.
  `test_scripts/multistatic_raytrace/cs_scene_pool_3d.py`.
- **Launchers**: pass every crop/geometry option explicitly (`--center --half ...`); copy the COMMON array from the reference
  launcher, never retype it. A driver default silently produced a non-comparable scene once.

### Shell and process hygiene (this box)
- `ps aux` is truncated by the rtk wrapper: check live processes with `pgrep -af <pattern>` plus `nvidia-smi` memory and log
  growth; never kill or relaunch on a bare `ps | grep` miss.
- `pkill`/`pgrep -f` self-match: bracket the pattern (`'[n]esz'`) AND keep the kill in its own Bash call with no other
  mention of the string (a `sed` path or `ls | grep` in the same call still matches and kills the call, exit 144).
- Never background a long job with a trailing `&` inside a foreground Bash call (pipes close, workers die with
  BrokenPipeError); use the tool's `run_in_background`.
- zsh does not word-split unquoted variables: multi-option launchers are bash scripts with arrays, run with `bash`.
- `local N=${#A[@]} L=$((N-1))` evaluates the arithmetic before the assignment; split the statements.
- `pixi run` uses the manifest of the current directory: after any `cd` (lint in `site3d`, a test dir) pass
  `--manifest-path .../site3d_sim/pyproject.toml` or run from `site3d_sim`, otherwise imports fail with a bogus
  ModuleNotFoundError.
- cProfile on cupy pipelines: a dominant `_thread.lock.acquire` is the tqdm monitor thread idling, not contention; cupy's
  Cython calls get no entries. Use py-spy + nvidia-smi for GPU wait time.

### Files and locations
- One-off test / diagnostic scripts live in `site3d_project_root/test_scripts/` (project root, outside the worktrees; never
  `/tmp`); run by path or with PYTHONPATH from a worktree.
- Generated docs/figures/decks go to the shared root dirs (`docs/`, `research/`, `summary/`, `ppt/`, `outputs/`), never inside
  a worktree; keep `docs/branches/<branch>.md` current for every `jisu/*` branch and read it first on session start.

@RTK.md
