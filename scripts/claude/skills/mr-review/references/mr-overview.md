# The MR architecture overview: a diagram of what the MR does, first

Jisu, Nov 23 2026: "start off with what the MR does in a diagram": a repo block diagram with the added and changed
files, then inputs, outputs and classes, so comments can refer back to it. Cards N-JA (down to functions; a small
change to an existing function or class opens with what that function or class does), N-JB (every review makes
one; an app button makes one for any MR), N-JC (all five levels), N-JD (a mini map plus a link in each thread's
explanation; boxes clickable). Plan and mock-ups: `site3d_project_root/outputs/mr_overview_mock/` (T-xi, Final N-JF).

## Contents
- Terms
- Who makes what
- Commands, in order
- The notes file
- What `check` refuses
- Thread anchors
- Files and the index entry
- Cold-read check
- Where it shows up
- Gotchas

## Terms

- **The five levels**: (1) **repo map**, the package tree with the MR's files and their classes and functions as
  boxes (green added, amber changed, pale amber changed only in a method, gray docstring only, red deleted);
  (2) **data flow**, the inputs, the new or changed code and the outputs, with types and shapes on the arrows;
  (3) **pipeline place**, where the MR sits in the site3d chain (raw data, `sar_common`, `image_formation`,
  `reconstruction`, `visualization_exports`, with `sar_simulation` feeding image formation) and who calls the code;
  (4) **class and function cards**, one per added or changed symbol: what it is or does, its signature, its
  fields or arguments with their docstring meaning, its callers; (5) **before vs after** for each changed behavior.
- **Box**: one file or one symbol of the MR. Its id is `<path>` for a file and `<path>::<Qualname>` for a symbol
  (`sar_simulation/phase_history/noise.py::add_noise`, `reconstruction/sar_image_reader.py::SIDDImageReader.get_metadata`).
  A test file is one box (its tests counted, not boxed).
- **Status** of a symbol: `added`, `changed`, `doc-only` (only docstrings differ; the code is the same `ast`),
  `via-methods` (a class whose own lines are unchanged but a method changed), `unchanged` and `removed` (listed in
  the facts, not boxes). A file: `added`, `changed` (renames included, read at the old path), `removed`.
- **`<head12>`**: the first 12 hex digits of the MR head SHA the overview was built at; **`<disc8>`**: the first 8
  hex digits of a GitLab discussion id.
- **Tags** on the page: "written" marks the reviewer's words (the notes file); "from the code" marks what the
  script read from the diff. A legend line under the page title says so.
- **Cold reader**: a fresh subagent that sees only the overview's words and a picture of levels 1 to 3, never the
  code, and says what the MR does (section "Cold-read check").

## Who makes what

| Part | Made by |
|---|---|
| Files, line counts, symbols and their status, signatures, dataclass fields, docstring argument lines, call sites, the repo map, thread anchors | `scripts/mr_overview.py` from git and `ast` (exact) |
| What this MR does (2 to 3 sentences), one description per box, the data flow, the pipeline note, what callers pass, before vs after, a terms list | the worker, in the notes file; `check` ties every name to the facts and the cold reader tests the words |

Call sites are resolved, not matched by name: `git grep -w` finds the files that name the symbol, then `ast`
resolves each use in its scope at the head. "Called from" lists a call whose callee resolves to the changed symbol:
a function through the file's imports (`from x import f`, `import x; x.f()`, aliases, a bare call in its own
module); a method or classmethod through `Class.m` or a receiver whose class an annotation, a constructor, a
return annotation or a field annotation gives; a class or its `__init__` where it is constructed. A property lists
its reads ("Read from"). A same-name function elsewhere or a receiver of another type is never listed; a receiver
whose type the code does not say goes under "Possible (unresolved receiver)". A use that hands the symbol over
is listed as "passed to <callee> in <caller>": the function or bound method given as an argument (`pool.imap(f,
xs)`, `ex.submit(obj.m)`, `key=f`), or, for a method, an instance of its class given to a repo function that uses
that method of its parameter, directly or through the functions it passes the parameter on to (a grid processor
given to `multiprocess_grid`, which reaches `pool.imap(item_processor.process, ...)`). A symbol with no use at
all says "no caller found in the repo". Not followed: dynamic dispatch (a call on a base-class receiver that a
subclass overrides) except through such a passed instance, an instance given to a function outside the repo,
loop and container element types, `getattr`.

## Commands, in order

All standard library: plain `python3` runs them. `S=~/.claude/skills/mr-review/scripts`,
`DIR=site3d_project_root/outputs/mr_context/<iid>` (another project: `<group>-<project>/<iid>`).

1. Read the full diff (SKILL.md step 3) and write `notes.json` (next section).
2. `python3 $S/mr_overview.py make <checkout> --base origin/main --head <MR head sha> --notes notes.json
   --threads threads.json --out $DIR` (threads from `mr_discussions.py --all --json`). It writes the files, prints
   each thread's box and the check; fix the notes until it says `ok` (warnings are allowed, problems are not).
   A SHA the checkout lacks stops it: `git -C <checkout> fetch origin` first.
3. Cold read (section below); rewrite the notes once if needed and rerun `make`.
4. `python3 $S/mr_context.py overview add $DIR --head <sha> --task <T-x> --cold-read <pass|rewritten|gap|pending>
   [--cold-read-note "<what the reader missed>"] --requested-by <review|button>`: lists it in `index.json`
   (refused when the check fails).
5. Verify: `python3 $S/mr_context.py check $DIR --overview <sha>`.
6. A review page: `python3 $S/mr_overview.py embed $DIR --head <sha> --into <briefing.html>` puts the overview
   under the page's `<h1>` (rerun replaces it).

The overview's header links the MR on GitLab and in the MR review app ("Open in MR app", `scripts/app_link.py`)
when the MR's number is known: `--iid`, else the output folder's name (`outputs/mr_context/<iid>`), else its
`index.json`. A scratch folder named otherwise takes `--iid <n>`.

`facts` / `build` / `check` are the three steps of `make` on their own, for a rebuild from saved facts.

## The notes file

```json
{
  "what": "2 to 3 plain sentences: the problem the MR solves and how.",
  "terms": {"NESZ": "noise-equivalent sigma zero: ..."},
  "pipeline": {"stage": "sar_simulation", "note": "Where it plugs in, in one sentence."},
  "boxes": {
    "<path>": "what the file is",
    "<path>::<Qualname>": "an existing symbol: what it does in general, first; a new one: what it is"
  },
  "callers_pass": {"<path>::<Qualname>": "what the callers pass in, in words"},
  "dataflow": {
    "layers": [["cfg", "geom"], ["cell"], ["add"], ["out"]],
    "nodes": {
      "cfg": {"label": "NoiseConfig", "sub": "nesz_db, nesz_reference", "kind": "changed", "box": "<box id>"},
      "geom": {"label": "Radar + motion", "sub": "paths (n, 3)", "kind": "data"}
    },
    "edges": [["cfg", "add", "NESZ dB", {"arg": "noise_config", "type": "NoiseConfig"}],
              ["add", "out", "+ noise", {"returns": "np.ndarray"}]]
  },
  "before_after": [{"box": "<path>::<Qualname>", "before": "what it did", "after": "what it does now"}]
}
```

- `stage`: one of `raw`, `sar_common`, `image_formation`, `reconstruction`, `visualization_exports`,
  `sar_simulation`, or null (another repo, or tooling off the pipeline).
- Node `kind`: `added`, `changed`, `data` (a value), `existing` (code the MR does not touch), `future` (dashed:
  a caller in a later MR). A node drawing a box names it in `box`, so a tap opens its card.
- Edge: `[from, to, label]` plus an optional object: `arg` (the parameter or dataclass field of the box it enters),
  `type` (that parameter's annotation, checked when given), `returns` (the return annotation of the box it leaves).
  Give `arg` on every edge into a function or class box: `check` then catches an arrow feeding the wrong thing
  (a T-xi cold reader found one: metadata drawn into a function that takes readers).
- Every box needs a description, except `doc-only` and `via-methods` ones; every `changed` symbol needs a
  before vs after. A test file's description says what its tests cover.
- Units and symbols the words use go in `terms` (the cold readers on both mock-ups asked for them).

## What `check` refuses

`mr_overview.py check` (and `make`, and `overview add`) exit 1 on:
- a note naming a box the MR does not have (in `boxes`, `callers_pass`, `before_after` or a data-flow node);
- a box with no description (except `doc-only`, `via-methods`); a `changed` symbol with no before vs after;
- an unknown pipeline stage; an empty `what`; a data-flow node in no layer, a layer naming no node, an edge to a
  missing node;
- an edge whose `arg` is not a parameter (or field) of the box it enters, whose `type` differs from that
  parameter's annotation, or whose `returns` differs from the return annotation of the box it leaves;
- the page with a `<script>`, an `on...=` handler, a `javascript:` link, an iframe, object or embed;
- a drawing (map, flow, pipe, mini maps) with any of those, a `foreignObject`, an image, an outside `url(...)`, or
  any link other than `#card-<box>`;
- a thread anchored to a box that is not listed.

Warning only: an edge into a function or class box that names no `arg`; a data-flow label that would still clip.
The drawing wraps a node name too wide for its box after `_ . / , -` or between a lowercase letter and a capital,
so three long names fit in one row; a piece with no such break point (`NOISECONFIGURATION` in a third of the width)
cannot wrap and warns, as does an edge label running off the drawing: put fewer boxes in that row. Edge labels draw
on top of the boxes, and an edge running down a side carries its label in the first gap below its source row.

`mr_context.py check <dir> --overview <sha>` adds: no `overview` entry, an entry at another head, missing or
mistyped keys, a page, data or drawing file missing, `rewritten` or `gap` without a note.

## Thread anchors

1. A line thread: the GitLab position's `new_path` and `new_line` at its `head_sha` (a removed line: `old_path`
   and `old_line` at `base_sha`), parsed with `ast`; the innermost def holding the line that is a box. None: the
   file box, with the reason (an unchanged method lands on its class when the class is a box).
2. A thread on the whole MR: the first backticked name in its notes equal to a symbol box's last name part.
3. Neither: null, the MR itself.

The anchor is computed at the thread's own head, so a later push that moves lines does not move the box. A head
the checkout lacks falls back to the file box ("that commit is not in the repo").

## Files and the index entry

In `$DIR`, beside the explanations:

```
overview-<head12>.html     the five levels, self-contained, no script; a light panel that reads the same on the
                           dark review page and in the app; between <!-- mr-overview:start/end --> markers
overview-<head12>.json     facts, notes, boxes, anchors, check result (the full record)
overview-<head12>.txt      the words, for the cold reader
overview-<head12>-map.svg, -flow.svg, -pipe.svg      the drawings, for the app to inline
overview-<head12>-mini-<disc8>.svg                   one mini map per thread that lands on a box
```

`index.json` gains one key, written only by `mr_context.py overview add` (temp file and rename):

```json
"overview": {
  "head_sha": "<40 hex>", "base_sha": "<merge base>", "made_at": "<ISO 8601 with offset>", "task": "T-xx",
  "requested_by": "review | button",
  "page": "overview-<head12>.html", "data": "overview-<head12>.json",
  "svgs": {"map": "...-map.svg", "flow": "...-flow.svg", "pipe": "...-pipe.svg"},
  "cold_read": "pass | rewritten | gap | pending", "cold_read_note": "",
  "boxes": [{"id": "<path>::<Qualname> or <path>", "kind": "file|class|function|method",
             "status": "added|changed|doc-only|via-methods|removed", "line": 330}],
  "threads": {"<discussion id>": "<box id or null>"}
}
```

Every box in the drawings is `<a href="#card-<slug>"><g data-box="<box id>">`, where `<slug>` is the box id
lowercased with every run of other characters turned into `-`. The page jumps without script; the app wires a tap
to its Code tab. The overview is valid at `head_sha`; when the MR head moves, the app shows "written at an older
version" with Refresh. Older overviews stay on disk (their head in the name).

## Cold-read check

As for explanations (`thread-explanation.md`): copy `overview-<head12>.txt` (as `overview.txt`) and a PNG of
levels 1 to 3 (a screenshot of the page's top) into an empty folder, and give a fresh subagent this prompt,
verbatim, with `<dir>` that folder. Compare with your own answers: ADDS, WHERE, FLOW and CHANGED match and
UNCLEAR names no defect of the overview (a wrong arrow, an undefined term, a cut sentence, an undescribed change):
`pass`. Otherwise one rewrite and a second reader; a match then is `rewritten`; still off, it ships as `gap` with
the gap named in `--cold-read-note`. UNCLEAR lines asking for more than an overview holds (why a threshold is
25 deg) are not defects.

```
You are a cold reader testing whether an MR architecture overview stands on its own. Read ONLY the files in <dir>
(overview.txt and every PNG figure there, opened with the Read tool). Do not open any other file, code,
repository or web page, and do not run commands other than listing that folder.

The overview describes one merge request. From the files alone, answer in at most 300 words, in exactly this format:

ADDS: what the MR adds or changes, in one or two sentences.
WHERE: which package and pipeline stage it sits in, and what calls the new or changed code (or that nothing does yet).
FLOW: the main inputs and outputs of the new or changed code, with types or shapes when the overview gives them.
CHANGED: for each existing function or class the MR changes, what it did before and what changes; "none" if the MR only adds.
UNCLEAR: anything you could not decide or had to guess from these files alone (a term not defined, a box with no
description, a figure that does not match the text). Write "none" if nothing.
```

## Where it shows up

- The review briefing: right under the title (`embed`), ahead of the verdict (`html-briefing.md`).
- Each thread's explanation block opens with that thread's mini map and "See it on the overview"
  (`thread-explanation.md`, item 1).
- The MR review app: the Overview tab's first card, boxes opening the Code tab, and an owner-only
  "Make overview" button that files a task once per head.

## Gotchas

- T-xi's mock-up notes (`runs/T-xi/inputs/notes_*.json`) predate `arg`: they pass with one warning per edge.
- The pipeline chain is site3d's; another repo gets no highlight (stage null).
- A Python file that does not parse at a head gives no symbols there (the file box stays).
