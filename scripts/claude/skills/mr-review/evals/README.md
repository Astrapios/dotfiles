# mr-review evals

Regression prompts built from `docs/branches/review-misses.md`. Each JSON follows the Anthropic
skill-eval shape (`query`, `expected_behavior`). Run one by starting a fresh Claude Code session
in the named worktree at the named SHA and issuing the query; grade against `expected_behavior`.
The skill-creator plugin's `run_eval.py` can drive them in batch. Not run automatically: a full
review costs minutes of agent time, so run them when this skill changes materially.
