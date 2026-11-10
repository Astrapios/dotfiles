---
name: study-gates
description: Gates G1 to G6 and audit checks A1 to A4 that a comparison study must pass before its numbers reach Jisu. Load for any study, benchmark, A/B or method comparison whose numbers reach Jisu; for any change to a solver, an extraction rule, a gate, a threshold or a score inside such a study; and for a desk auditing one.
---

# Study gates: what every comparison study must pass before its numbers reach Jisu

A deck or report page that skips a gate is not sent. The desk pastes the Gates section into every study brief and
checks each gate in its audit.

Why this exists (Nov 9 2026, sparse against BP; Jisu: "come up with a mitigation plan so dumb mistakes like this never happens again"):
no base run was reproduced, and a noise gate went onto the sparse result with no known-answer test.
BP and sparse were treated unequally (a new extraction rule for one, a gate derived for the other's amplitudes).
An "unexplained" 6 dB energy gap shipped on slides as an open item instead of stopping the work.

## Gates (paste into every study brief)

G1. Base run first. The brief names the known-good run (path, settings, its numbers). The first deliverable is its
    reproduction with today's code. No base run known: the worker searches research/, ppt/, summary/, docs/branches/
    and the memory index for the method and reports what exists before running anything.

G2. Change table. Every change to a solver, an extraction rule, a gate, a score or the scored cells is one row:
    what changed, why (round, card), and the score of EVERY compared method on the base case before and after. One
    change per row. The table ships with every deck.

G3. Known-answer test. Before two methods are compared, an isolated point target with known height and level goes
    through each method's whole chain at every noise level: peak height, width, lobe levels and peak power against the
    prediction. Every new gate or threshold shows that it keeps a target that should be seen and drops noise alone.

G4. Equal treatment. A change to one method's extraction or gating is applied to the other, or the brief says why
    not and the change table shows both.

G5. Expectations written before the run. Three or four lines of what must hold (examples: noise can only make a
    method worse, and smoothly; a method does not lose heights at signal to noise above 10 dB; a sparse solve with a
    large weight reads the BP peak; a later round does not reverse an earlier accepted result without a named cause).

G6. Stop on a violation. A result that breaks G5, contradicts an earlier accepted result, or carries the word
    "unexplained" stops the deck. It goes to the desk at once with its evidence, and to Jisu as a card. It is never
    an "open item" on a slide of a deck that also draws conclusions from it.

## Desk audit (added to the audit of every study result)

A1. Look at the key maps at full size, next to the base run's maps, before reading any median.

A2. Search for earlier results on the same method (memory index, research/, ppt/, the brief's base run) and write
    "earlier results checked: <paths>, agree / differ because <cause>" into audits.md. No entry, no send.

A3. Check G1 to G6 one by one and record each as passed, failed or not applicable with the file that shows it.

A4. For a deck Jisu will present: one independent physics review (a fresh reviewer given the maps and the change
    table, not the conclusions) before it is sent.
