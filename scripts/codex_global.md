# Global Codex Instructions

The rules after this header are Jisu's working rules, the same files Claude Code reads (~/.claude/CLAUDE.md and its imports). They were written for Claude Code; this header says how they apply in Codex. astra writes this file (~/.codex/AGENTS.md) from them, so edit the rules or ~/.dotfiles/scripts/codex_global.md, never this copy.

- Skills: the same skill folders as Claude Code are linked into ~/.codex/skills (html-report, long-run, deeper, glb-view, mr-review, study-gates, resolve-conflicts, vault-sync).
- A rule or skill that names a Claude Code tool (Bash, Read, Edit, Write, ScheduleWakeup, Monitor, Agent or a subagent type, the Skill tool, AskUserQuestion, plan mode, Artifacts, memory files): use Codex's nearest equivalent (its shell for Bash and reads, apply_patch for edits), or skip that step and say so.
- No hook adds the memory cap or the rtk rewrite in your own Codex sessions (astra's Codex workers get astra's hooks): type `prlimit --as=...` yourself in every compute command, as Resource Limits shows.
- Claude Code's hooks do not run here, so follow what they enforce yourself: MR and PR comments stay drafts (never publish, post a plain note or resolve a thread), and no attribution lines naming Codex, OpenAI or Claude.
- Reports and figures reach Jisu the same way: the html-report skill, `astra send-doc` and `astra send-photo`. `--archive` needs `ASTRA_ARCHIVE_DIR`, which only Claude Code's settings set: prefix `ASTRA_ARCHIVE_DIR=/home/ubuntu/src/site3d_project_root/ppt` when it is unset.
