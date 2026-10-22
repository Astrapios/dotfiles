# Posting review comments on the MR (only when the user asks)

Read fully before posting anything. The global hook `guard_gitlab_publish.py` blocks publishing,
plain notes and thread resolution; everything below is what to do within that boundary.

## Contents
- Scripts (use these, do not hand-roll glab calls)
- Thread design rules
- Suggestions vs diff blocks
- Content rules (what a comment may contain)
- Editing drafts the user may be touching

## Scripts (in `scripts/`, run from the MR's worktree)

- `mr_discussions.py [IID] [--all] [--json]` lists threads with anchor, state, author, first line.
  Default is open threads only; `--all` includes resolved. The discussions API is the only
  authoritative source: `glab mr view --comments` drops anchors and resolved state.
- `draft_note.py` creates or edits DRAFT notes with correct GitLab line codes:
  - new anchored thread: `--path F --line A --line-end B --note-file note.md`
    (range end becomes `position.new_line`, as a drag-select in the web UI would)
  - with a suggestion: add `--suggestion-file fix.txt`; the block becomes
    ```suggestion:-(B-A)+0``` and replaces exactly lines A..B
  - reply in a thread: `--reply-to <discussion_id> --note-file reply.md`
  - edit: `--update <draft_id>` with the same anchor flags (the position is always resent;
    a PUT with only `note` wipes it)
  - `--list`, `--delete <id>`, `--dry-run` (prints the payload)
  The script computes `sha1(new_path)_<old_pos>_<new_pos>` from the base..head diff, so
  unchanged lines outside hunks get the right old line number and added lines get type new.

## Thread design rules

- **One thread per item**, anchored on the code it is about, with a multi-line range over the
  relevant lines. When the cause lives elsewhere, add a short comment anchored there too.
  Only cross-cutting notes (a short summary, a missing-tests list) are general comments.
- **Split a multi-hunk fix** into one short thread per hunk, each with its own suggestion;
  the main thread carries the explanation, the others say what they pair with.
- **Every comment carries an ask.** No FYI-only comments ("worth keeping", "same as the other
  thread"). If a finding implies an action, state it as a request. The summary note is the
  only exception.

## Suggestions vs diff blocks

- Use a GitLab suggestion whenever the change replaces exactly the anchored lines. It renders
  red/green with an "Apply suggestion" button once published. In unpublished drafts it may show
  as a plain white block; tell the user it renders after publishing.
- Fall back to a ```diff``` block (`-` current lines from HEAD, `+` new lines) only when a
  suggestion cannot apply: another file than the anchor, non-contiguous lines in one thread,
  pseudo code, or a published thread whose anchor cannot move.

## Content rules

- **Team language only.** No internal labels (B1, S1, D1, G1, T1); name the problem in words
  ("the crash at `max_bounces >= 2`").
- **Only what this MR needs.** No review history, no references to earlier MRs or the user's
  other work, no demo or scene names the author did not use, no speculative follow-ups.
  Each comment: the problem, the evidence (a figure and one or two numbers), the ask.
- **Cap the nits** at three style / naming / wording threads per MR; fold the rest into the
  summary note or leave them in the HTML briefing. Soundness findings have no cap.
- **Pre-existing findings are not review comments** unless the MR touches those lines or the
  user asks; when posted, the first line says it predates this MR.
- **Realistic scenarios only.** Drop failures that only occur in test geometries (radar inside
  a closed box) unless they can happen in real use.
- Markdown in the terminal: write drafts the user will paste as raw markdown in a fenced block.

## Editing drafts the user may be touching

The user edits drafts in the web UI while you work: `--list` (re-fetch) before any change,
edit only what was asked, and never overwrite a draft the user may be editing without asking.
