#!/usr/bin/env python3
"""Context advisor (UserPromptSubmit): on each prompt, has Claude judge whether to suggest /clear or /compact.

The hook cannot tell whether a prompt starts a new task; Claude can. So on every prompt, once the conversation holds
more than a little context, the hook hands Claude a short note with the context size and the rule: a prompt that
starts a different task gets a one-line suggestion to write the handoff into the durable notes and /clear; a prompt
that continues a task whose context has grown large gets a /compact suggestion with a drafted focus line; anything
else gets no comment.

The context size is the last main-thread assistant turn's prompt (input + cache read + cache creation tokens), read
from the tail of the transcript; subagent (sidechain) turns are skipped. Knobs (environment):
  CONTEXT_ADVISOR_FLOOR    below this many tokens, no note (nothing worth clearing yet); default 20000
  CONTEXT_ADVISOR_COMPACT  from this size on, a long-running task is worth a /compact; default 250000
  CONTEXT_ADVISOR_OFF=1    no note at all
An Astra View area's desk or worker gets no note (`astra role --json`): a large desk is replaced with New desk, which
the desk's bar suggests, and a worker lives for one task. When astra is missing, fails or is slow, the note goes as
before.
Any error is swallowed: advice must never get in the way of a prompt.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

FLOOR = int(os.environ.get("CONTEXT_ADVISOR_FLOOR", "20000"))
COMPACT = int(os.environ.get("CONTEXT_ADVISOR_COMPACT", "250000"))
TAIL = 4 * 1024 * 1024   # the last main-thread turn is always near the end of the transcript


def context_tokens(transcript: str) -> int | None:
    """Prompt size of the last main-thread assistant turn in the transcript, or None when there is none yet."""
    p = Path(transcript)
    if not p.is_file():
        return None
    with p.open("rb") as f:
        size = f.seek(0, 2)
        f.seek(max(0, size - TAIL))
        lines = f.read().splitlines()
    for raw in reversed(lines):
        try:
            rec = json.loads(raw)
        except ValueError:
            continue   # the first line of the tail may be cut
        if rec.get("type") != "assistant" or rec.get("isSidechain"):
            continue
        usage = (rec.get("message") or {}).get("usage") or {}
        if usage:
            return sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return None


def in_area(session_id: str) -> bool:
    """True when astra says this session is an area's desk or worker; False for anything else, including no astra,
    an error or no answer within a second."""
    astra = os.path.expanduser("~/bin/astra")
    try:
        r = subprocess.run([astra, "role", "--json", "--session", session_id], capture_output=True, text=True, timeout=1)
        return json.loads(r.stdout).get("role") in ("desk", "worker")
    except Exception:
        return False


def note(tokens: int) -> str:
    large = tokens >= COMPACT
    size = f"~{round(tokens / 1000)}k tokens" + (" (large)" if large else "")
    k = f"~{round(tokens / 1000)}k"
    compact = ("If it continues the current task: the context is large, so your reply MUST end with the line "
               f"`Context: {k} tokens. Recommend /compact:` followed by a fenced code block holding ONE line the user "
               "copies and runs as is: `/compact Keep: ... State: ... Next: ... Drop: ...`. Fill it from this "
               "conversation, specific enough to resume cold. Keep: the workflow and rules in force (the exact steps "
               "and commands, user preferences and corrections given in this session, decisions made and why). State: "
               "exact versions, branches, worktree paths, background agent IDs and what each is doing, running jobs, "
               "log and output paths, failing tests. Next: the remaining steps in order, including who to tell what. "
               "Drop: what the summary can lose (per-run logs, superseded attempts, screenshot descriptions, earlier "
               "histories). Names, paths and IDs, never placeholders or categories; one line, as long as it needs "
               "(often 100-250 words), no line breaks." if large else
               "If it continues the current task: say nothing about context.")
    return (f"[context-advisor] Context so far: {size}. The user sees only your reply (Astra View), never "
            "this note, so a recommendation you leave out is lost. Before answering, judge this prompt against the "
            "task in progress. A different task means a different feature, bug, repo area or question whose answer "
            "does not need this conversation's reasoning; a follow-up, a fix or a refinement of the current work is "
            "the same task, and so is a quick aside you can answer in a few lines. "
            f"If it starts a different task: your reply MUST open with this line: `New task, context {k} tokens: "
            "recommend writing the handoff into the notes, then /clear.` Then offer to write the handoff (decisions, "
            "state, next steps) into the durable notes (branch summary, summary/, findings or plan file, memory), and "
            "wait for the user's choice before starting the new task. "
            f"{compact} "
            "Make a suggestion once per task: if the user already declined or ignored it for this task, do not repeat "
            "it. Never run /clear or /compact yourself.")


def main() -> int:
    if os.environ.get("CONTEXT_ADVISOR_OFF") == "1":
        return 0
    try:
        ev = json.load(sys.stdin)
        tokens = context_tokens(ev.get("transcript_path") or "")
        if tokens is not None and tokens >= FLOOR and not in_area(ev.get("session_id") or ""):
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": note(tokens)}}))
    except Exception:   # never block a prompt over advice
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
