---
name: resolve-conflicts
description: Resolve merge conflicts during git rebase or git-spice restack. Use when the user encounters merge conflicts, says "fix conflicts", or is in the middle of a rebase/restack.
allowed-tools: Read, Edit, Grep, Glob, Bash
---

# Resolve Merge Conflicts

Resolve all merge conflicts in the current repository, then continue the rebase.

## Steps

1. Find all files with conflict markers:

!`grep -rn '<<<<<<< ' --include='*.py' --include='*.md' --include='*.toml' --include='*.yaml' --include='*.yml' --include='*.json' . 2>/dev/null | head -50`

Current rebase status:

!`git status 2>&1 | head -20`

2. For each conflicted file:
   - Read the file to understand both sides of the conflict
   - Determine which changes to keep based on context:
     - If the base branch refactored/renamed something, apply the upstack branch's logic using the new names/structure
     - If both sides added different things, merge both additions
     - If one side deleted and the other modified, prefer the modification unless the deletion was intentional cleanup
   - Remove all conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
   - Verify the result is syntactically valid

3. After resolving all conflicts:
   - Run `pixi run -e lint ruff check <files>` on resolved Python files
   - Stage resolved files with `git add <files>`
   - Continue with `GIT_EDITOR=true gs rebase continue` — NOT plain `git rebase
     --continue`. Per git-spice's own docs, `gs rebase continue` works in place of
     `git rebase --continue` even when no git-spice operation is in progress, and when
     the rebase WAS started by `gs stack restack` / `gs sr` / `gs upstack restack`, it
     resumes the interrupted git-spice operation and keeps restacking the remaining
     branches (plain `git rebase --continue` would finish only the current branch and
     silently abandon git-spice's continuation state). Fall back to plain
     `git rebase --continue` only if `gs` is unavailable.

4. If the continue stops on more conflicts (same branch or the next branch in the
   stack), repeat from step 1.

5. Finish the stack: once no rebase is in progress, run `gs ls` and check for
   `(needs restack)` markers.
   - If any remain, run `gs stack restack` (and resolve any new conflicts per steps 1-4).
   - A branch checked out in ANOTHER worktree cannot be restacked from here — git
     refuses to rebase a branch that is checked out elsewhere. Restack it from its own
     worktree (`gs branch restack` there), or tell the user which worktree needs it.

6. Report what was resolved, which branches were restacked, and the final `gs ls` state.

## Rules

- Never use `git checkout --ours` or `--theirs` to blindly pick one side
- Always read and understand both sides before resolving
- Preserve the intent of BOTH branches when possible
- If a conflict is ambiguous, ask the user before resolving
