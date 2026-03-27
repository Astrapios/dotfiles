# Global Claude Code Instructions

## Git Conventions

- Do NOT add "Co-Authored-By" lines or any Claude/AI attribution to commit messages.

## Working Style

- NEVER ask the user to test speculative fixes. Investigate and verify logic yourself first.
- When dealing with terminal UI interactions (tmux send-keys), capture the actual pane content at each step to understand the UI state before writing key sequences.
- Think through the full execution path before making changes — trace through the code, consider timing, and verify assumptions.

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

When the user asks you to show, send, or share an image, figure, chart, screenshot, or any visual file to Telegram, use:

```bash
astra send-photo /path/to/file.png "optional caption"
```

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
