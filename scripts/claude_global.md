# Global Claude Code Instructions

## Git Conventions

- Do NOT add "Co-Authored-By" lines or any Claude/AI attribution to commit messages.

## Working Style

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

## Active Projects

When working on a feature area, check for a project notes file in `site3d_project_root/projects/` first. These contain accumulated lessons, debugging strategies, and design decisions. If a project file doesn't exist for the current work, create one following the same pattern.

Current project files:
- `projects/dof-height-estimation.md` — DOF-based height estimation from SAR imagery

@RTK.md
