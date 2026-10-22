#!/usr/bin/env python3
"""PreToolUse(Bash) guard: MR review output stays as DRAFTS that the user publishes.

Blocks (exit 2, reason on stderr):
  - publishing GitLab draft notes (draft_notes/bulk_publish, draft_notes/<id>/publish)
  - creating plain MR notes / discussions / thread replies (POST to .../notes or .../discussions)
  - resolving MR discussion threads (PUT ... resolved)
  - glab mr note|comment|approve|unapprove|merge, gh pr review|comment|merge
Everything else passes through (exit 0, no decision). Registered in claude_settings.json.
"""
import json
import re
import sys

RULES = [
    (r"merge_requests/\d+/draft_notes/(bulk_publish|\d+/publish)",
     "publishing draft notes is reserved for the user (CLAUDE.md: MR comments stay drafts)"),
    (r"(?:-X\s*POST|--method[\s=]*POST|-X\s*PUT|--method[\s=]*PUT).*?merge_requests/\d+/(notes|discussions)\b"
     r"|merge_requests/\d+/(notes|discussions)(/[0-9a-f]+/notes)?(?![^\s'\"]*\?).*?(?:\s-f\s|\s-F\s|--field|--raw-field|--input|-X\s*POST|-X\s*PUT)",
     "plain MR notes / discussion replies must be draft notes: POST merge_requests/<iid>/draft_notes"),
    (r"discussions/[0-9a-f]+.*\bresolved\b|\bresolved\b.*discussions/[0-9a-f]+",
     "resolving MR threads is reserved for the user"),
    (r"\bglab\s+mr\s+(note|comment|approve|unapprove|merge)\b",
     "glab mr note/comment/approve/merge are reserved for the user; use draft notes"),
    (r"\bgh\s+pr\s+(review|comment|merge)\b",
     "gh pr review/comment/merge are reserved for the user"),
]


def main() -> int:
    raw = sys.stdin.read()
    # cheap pre-filter before parsing JSON
    if not re.search(r"\bglab\b|\bgh\s+pr\b|merge_requests/\d+", raw, re.I):
        return 0
    try:
        cmd = json.loads(raw).get("tool_input", {}).get("command", "")
    except Exception:
        return 0
    for pat, why in RULES:
        if re.search(pat, cmd, re.S | re.I):
            sys.stderr.write(f"Blocked by guard_gitlab_publish: {why}. Command: {cmd[:200]}\n")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
