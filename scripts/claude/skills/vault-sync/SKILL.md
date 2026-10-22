---
name: vault-sync
description: Incrementally update the Obsidian knowledge vault at /home/ubuntu/src/site3d_project_root/obsidian/ — mine new concepts, gotchas, recipes, and lessons from the Claude memory dirs, research/, docs/, docs/branches/, projects/, and summary/ since the last sync, refactor them into linked vault notes, and refresh MOCs. Use when the user says "/vault-sync", "update the vault", "sync my obsidian notes", or after a work stretch produced new durable findings.
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Agent
---

# Vault Sync

Incrementally fold new durable knowledge into the Obsidian vault at
`/home/ubuntu/src/site3d_project_root/obsidian/` (the VAULT below). The vault is a
**derived layer**: it distills sources, never absorbs or modifies them. Only
concept-level content belongs — concepts, gotchas, recipes, lessons. Never branch
status, session chronology, or TODOs. The test: *would this still be true and useful if
the branch/session it came from were forgotten?*

Use the exact embedded commands below — do not re-derive them (they encode decisions
recorded in the vault's `vault-design-decisions.md`).

## Steps

### 1. Load state

Read `VAULT/vault-conventions.md` (note conventions, tag vocabulary, source roots) and
`VAULT/vault-state.md`. The machine ledger is the fenced ` ```ledger ` block:
one `path<TAB>mtime-epoch<TAB>size-bytes` line per previously examined source file.

### 2. Diff sources against the ledger

```bash
VAULT=/home/ubuntu/src/site3d_project_root/obsidian
ROOT=/home/ubuntu/src/site3d_project_root
awk '/^```ledger/{f=1;next}/^```/{f=0}f' "$VAULT/vault-state.md" | sort > /tmp/claude-1000/ledger.old
# Single find with -printf: do NOT pipe file lists through xargs/stat — the rtk shell
# wrapper on this machine decorates mid-pipeline output and corrupts it.
find /home/ubuntu/.claude/projects/-home-ubuntu-src-site3d-project-root-*/memory \
     "$ROOT/research" "$ROOT/docs" "$ROOT/docs/branches" "$ROOT/projects" "$ROOT/summary" \
     -maxdepth 1 -name '*.md' ! -name 'MEMORY.md' -printf '%p\t%T@\t%s\n' 2>/dev/null \
  | sort > /tmp/claude-1000/ledger.new
# Candidates: new path, or mtime/size changed
comm -13 /tmp/claude-1000/ledger.old /tmp/claude-1000/ledger.new | cut -f1
# Vanished sources: in ledger but no longer on disk
comm -23 <(cut -f1 /tmp/claude-1000/ledger.old | sort) <(cut -f1 /tmp/claude-1000/ledger.new | sort)
```

- Candidate files get re-read **whole** (no tail/delta mining — deliberate decision).
- For each vanished source: remove its ledger line and add a `stale-source` tag to any
  vault note whose `sources:` cites that absolute path (`grep -rlF "<path>" VAULT/*.md`).
  Never auto-delete a note — flag it and report it.

If there are no candidates and no vanished sources, report "nothing new to sync" and stop.

### 3. Mine candidates, dedupe first

- Re-read each candidate file whole. A large branch doc may go to one subagent, seeded
  with the vault's current note-title list (`ls VAULT/*.md`) so it dedupes as it reads.
- For each durable finding, grep the vault (filenames + key terms) **before** creating
  anything. Prefer updating the existing note; only genuinely new topics get new notes.
- **Surgical updates only**: edit the relevant section of an existing note; never
  regenerate a note wholesale — the user hand-edits vault notes. Superseded claims are
  rewritten in place, not appended chronologically.

### 4. Write per conventions

- New notes follow `vault-conventions.md` exactly: kebab-case filename; exactly one
  kind-tag (`concept`/`gotcha`/`recipe`/`lesson`) + ≥1 domain tag; `created:` today;
  `sources:` absolute paths.
- A new domain tag is allowed only if you also append it to the vocabulary table in
  `vault-conventions.md` in the same run.
- Each new note links out to related notes and gets one line in the right `moc-*` note.
  Do not maintain reverse links in existing notes — Obsidian computes backlinks.

### 5. Verify

Broken-wikilink check (empty output = pass). Strips code fences and inline code first
(Python `x[[0, 1]]` is a false positive otherwise), then `|display` / `#heading` / `.md`:

```bash
cd /home/ubuntu/src/site3d_project_root/obsidian
for f in *.md; do sed '/^```/,/^```/d' "$f" | sed 's/`[^`]*`//g' | grep -oE '\[\[[^]|#]+'; done \
  | sed 's/^\[\[//; s/\.md$//; s/ *$//' | sort -u \
  | while read -r t; do [ -e "$(echo "$t" | tr 'A-Z' 'a-z').md" ] || echo "BROKEN: [[$t]]"; done
```

Orphan check — every content note must be cited somewhere (MOCs/meta exempt; empty = pass):

```bash
cd /home/ubuntu/src/site3d_project_root/obsidian
for f in *.md; do n="${f%.md}"; case "$n" in home|vault-*|moc-*) continue;; esac
  grep -lF "[[$n" *.md | grep -qv "^$f\$" || echo "ORPHAN: $n"; done
```

Fix all failures before finishing.

### 6. Update state and report

- Replace the ` ```ledger ` block contents in `vault-state.md` with
  `/tmp/claude-1000/ledger.new` (every file examined this run gets its current row —
  including files that yielded nothing, so they aren't re-scanned forever).
- Update "Last sync:" and prepend one changelog line; keep only the newest ~20 entries.
- Report: notes added / updated / flagged `stale-source`, and any vocabulary additions.

## Rules

- **Never modify source files** — memory notes, research docs, summaries, branch docs
  are read-only inputs.
- Concepts only: no status, chronology, TODOs, or MR bookkeeping in vault notes.
- Wikilinks resolve by **filename only** — never rely on aliases; every `[[link]]`
  targets an existing note filename.
- Takeaway-first bodies; split any note that grows past ~120 lines.
- When a convention itself changes, record the decision and rationale in
  `vault-design-decisions.md`.
