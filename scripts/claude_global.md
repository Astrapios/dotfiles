# Global Claude Code Instructions

## Working Style

- **Context hygiene: clear between tasks, compact within one; the files carry the context.** On every prompt past 20k tokens of context, a hook (`~/.claude/hooks/context_advisor.py`, UserPromptSubmit) hands me the size and asks me to judge the prompt against the task in progress. A different task: open the reply with one line saying so, offer to write the handoff (decisions, state, next steps) into the durable notes (`docs/branches/<branch>.md`, `summary/`, the findings or plan file, memory), recommend `/clear`, and wait for the user's choice before starting it. The same task past 250k: end the reply with a fenced, copy-paste-ready one-line `/compact Keep: ... State: ... Next: ... Drop: ...` filled with real names, paths, versions, agent IDs and the session's rules (Keep = workflow, rules and decisions; State = versions, branches, worktrees, agents, jobs, logs, failing tests; Next = remaining steps in order; Drop = logs, superseded attempts, screenshot descriptions). Otherwise no comment. Once per task; the user runs `/clear` and `/compact`, never me. Knobs: `CONTEXT_ADVISOR_FLOOR`, `CONTEXT_ADVISOR_COMPACT`, `CONTEXT_ADVISOR_OFF=1`.

@shared_rules.md

@RTK.md
