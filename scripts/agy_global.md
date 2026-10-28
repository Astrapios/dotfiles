# Global Antigravity (agy) Instructions

The rules after this header are the user's working rules, the same file Claude Code reads. They were written for Claude Code; this header says how they apply in agy.

- Skills: html-report, glb-view, long-run, deeper and resolve-conflicts are installed for agy too (same files). mr-review and vault-sync stay with Claude Code.
- A rule or skill that names a Claude Code tool (Bash, ScheduleWakeup, Monitor, Agent or a subagent type, the Skill tool, AskUserQuestion, plan mode, Artifacts, memory files): use agy's nearest equivalent (run_command for Bash), or skip that step and say so.
- No hook adds the memory cap or the rtk rewrite in your own agy sessions (astra's agy workers get both from astra's hook): type `prlimit --as=...` yourself in every compute command, as Resource Limits shows.
- Claude Code's hooks do not run here, so follow what they enforce yourself: MR and PR comments stay drafts (never publish, post a plain note or resolve a thread), and no attribution lines naming Gemini, Antigravity or Claude.
- Reports and figures reach the user the same way: the html-report skill, `astra send-doc` and `astra send-photo`.

@[Shared working rules](shared_rules.md)
