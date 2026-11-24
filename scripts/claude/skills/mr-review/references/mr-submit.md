# Submitting an MR: description drift check and "Design choices and known limits"

Use this when Jisu says "submit the MR", "write the MR description", or asks for a `gs branch submit` of a new MR,
and again before any `gs branch submit --update-only` (or a description edit) after the code changed. Reviewers have
spent comments on descriptions that named code the branch no longer had (removed solvers, a renamed helper, "28
tests" for 36), and most line threads landed on choices the description never stated. Both are cheap to catch here.

## Contents
- 1. Description rules (unchanged)
- 2. Drift check, then fix the description
- 3. Design choices and known limits (proposed, Jisu accepts or edits)
- 4. Submit or update

## 1. Description rules (unchanged)

- First line `Closes #N` or `Related to #N` (search open issues first; several MRs can answer one issue).
- Describe what main gains, not the journey inside the branch. No list of files changed. No test plan section.
- Title in commit tone: imperative verb first.
- Team language: no review labels, no review history, no mention of AI or agents, no em or en dashes.

## 2. Drift check, then fix the description

Write the description to a file, then run the check against the head you are about to push (read only):

```bash
S=~/.dotfiles/scripts/claude/skills/mr-review/scripts
python3 $S/description_drift.py --description desc.md --head <branch or sha> --repo <checkout> [--target main]
# an existing MR, as it stands on GitLab (GET only):
python3 $S/description_drift.py --mr <iid> --repo <checkout> --out <folder>
```

It lists, one line each with the description line number:
- code names (backticks, snake_case, CamelCase, dotted modules, paths, `--flags`) not at the head;
- acronyms not found in the MR's own files (a removed method such as OMP, or prose such as NVIDIA);
- words found nowhere in the repository (a removed method such as "debiasing", or plain English such as "eleven");
- each "N tests" claim against the test functions the MR adds and changes, per file.

Fix every real item before the submit or update: delete or rename the gone name, correct the count (say what is
counted, e.g. "36 tests: 30 for the solvers, 6 for GPU and geometry helpers"). Leave prose false positives alone.
Rerun until only false positives remain, and say in one line which listed items are prose.

The base is the merge-base with `origin/<target>`: fetch first, or pass `--base` for a stacked branch (its parent
branch). A head that is not in the checkout needs `git fetch origin`.

## 3. Design choices and known limits (proposed, Jisu accepts or edits)

Propose up to 5 bullets for a "Design choices and known limits" section. Each bullet is one settled choice the
code has already made, why, and the alternative not taken; or one limit a reviewer would otherwise find. Pick the
choices a reviewer is most likely to question: defaults and fixtures (iteration counts, thresholds, a skipped GPU
path), conventions (frames, signs, look direction), a duplicated helper, a public name, what is not validated.

Rules:
- Only choices visible in the diff at the head; never a plan or a choice a later MR will make.
- Team language, written as the author's choice ("defaults to 600 iterations, which is not converged, because ...;
  a converged default was not taken because ..."). No labels, no "Claude", "agent" or "AI", no dashes.
- Read the existing "Design" and "Known limits" sections first; merge into them rather than repeat.
- Show the bullets to Jisu as a fenced code block (raw markdown) to accept or edit. Nothing goes into the
  description until Jisu says so; then add the accepted bullets under `## Design choices and known limits`.

Example shape (both bullets restate !782's own description at f0f483ae9):

```
## Design choices and known limits
- The prior height window is enforced inside FISTA as an exact restricted solve; zeroing atoms after an
  unrestricted solve was not used because an aliased replica atom takes part of the energy.
- `tomo_fista` defaults to 600 iterations, which is not converged: 2400 iterations raise the atom level by 5.2 dB.
```

## 4. Submit or update

Use git-spice: `gs branch submit --branch <b> ... --dry-run` first, then without; a push or a new MR needs
Jisu's yes. After the push, rerun the drift check with `--mr <iid>` to confirm the description on GitLab
matches the pushed head.
