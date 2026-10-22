---
name: deeper
description: Make the current task deeper, not bigger. Use when the user says "/deeper", "go deeper on this", or wants the task re-asked the way a top professional in the field would frame it. Works on the task given as the argument, or on the task currently being worked when no argument is given.
---

# Deeper

Produce a deeper version of the task, not a bigger one. Do NOT start the work; the deliverable of this skill is the
analysis below plus the rewritten prompt. Keep every section tight: concrete nouns, numbers and names, no generalities.

Task to deepen: $ARGUMENTS (if empty: the task the conversation is currently working on, stated in one line first).

## Before rewriting the task, answer, in order

1. **What a top professional in this field would do here that is not being asked for.** The one or two moves an
   expert would consider mandatory and the current framing skips: the check they would run first, the control they
   would insist on, the question they would answer before touching the asked-for one.
2. **The rawest material they would start from** instead of what has already been digested. Name the actual inputs
   (raw data, phase history, primary measurements, source documents, the unprocessed product) and say what the
   digested version hides.
3. **The specific methods and tools they would use.** Named techniques, estimators, instruments, references, scripts
   or code paths, with the parameter that matters for each. No "advanced analysis".
4. **What, other than the two of us, would show the result is right.** The independent evidence: a physical
   invariant, a simulation with known truth, an external dataset, a prediction that must hold, a reviewer or a
   published result. State the number it must produce.

## Then

5. **Rewrite the prompt** to ask for exactly that: one paragraph, in the user's voice, with the inputs, methods,
   acceptance evidence and the scope boundary ("not bigger") made explicit. Put it in a fenced code block so it can
   be copied.

## House rules
- Verdict first, then the five sections; no preamble, no restating the original task beyond one line.
- Where the project already has the raw material or the tool (scripts, caches, truth data), name the path.
- If a step cannot be answered honestly, say "none" and why; do not invent an expert move.
