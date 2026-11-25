#!/usr/bin/env python3
"""Check the merge of two heads for names that one side removed and the merged tree still uses.

Usage:
    merge_names.py <repo> <head A> <head B> [--base REF] [--json] [--out DIR] [--exit-zero]

`git merge-tree` reports textual conflicts only. A merge can be conflict free and still broken: a moved function
body keeps a parameter the other side never had, or one side renames a keyword while the other side adds a call
with the old name. This script builds the merged tree with `git merge-tree --write-tree` (no checkout, no ref
moved; git only adds the merged objects to the object store) and reports:

  (a) the textual conflicts, with the marker line range of each hunk in the merged file;
  (b) every public name (function, class, method, attribute, keyword argument) that one side removed or renamed
      and that the merged tree still references, in or outside the conflicted hunks, on lines the removing side
      does not have itself;
  (c) undefined names, unused locals, redefined names and unused imports (ruff F821, F841, F811, F401) in the
      merged versions of the .py files either side changed. Head A is the target (main): a finding head A already
      has, on a line with the same text, is pre-existing and does not count. One head B already has still counts,
      since merging B brings it in; it is tagged "also in B" (after a rebase onto A, A is an ancestor of B, the
      merged tree is B itself, and this is the only way the check can see a break the rebase carried in).
      Findings that hold whatever way the conflict hunks are resolved are listed first.

A conflicted file is analyzed twice, once with every hunk resolved to A's text and once to B's, and line numbers
are mapped back to the merged file with its markers.

Exit status: 0 clean, 1 findings (0 with --exit-zero), 2 usage or git error. --out DIR also writes
merge_names.txt and merge_names.json there.
Ruff comes from $MERGE_NAMES_RUFF, the repo's pixi ruff (<repo>/.pixi/envs/{lint,dev,default,test}/bin/ruff, else
`pixi run --manifest-path <repo> -e lint ruff`), PATH or `uvx ruff`; pyflakes is the fallback.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MARK_OURS, MARK_BASE, MARK_SEP, MARK_THEIRS = "<<<<<<< ", "||||||| ", "=======", ">>>>>>> "
SELF_NAMES = {"self", "cls"}
DATA_BASES = {"BaseModel", "NamedTuple", "TypedDict"}


class GitError(RuntimeError):
    pass


def git(repo: str, *args: str, ok: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", repo, *args], check=False, capture_output=True, text=True)
    if proc.returncode not in ok:
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc


def show(repo: str, rev: str, path: str) -> str | None:
    proc = subprocess.run(["git", "-C", repo, "cat-file", "-p", f"{rev}:{path}"], check=False, capture_output=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- merge tree and conflict markers


@dataclass
class Conflict:
    path: str
    kind: str
    message: str
    regions: list[tuple[int, int]] = field(default_factory=list)


def merge_tree(repo: str, a: str, b: str, base: str | None) -> tuple[str, list[Conflict]]:
    args = ["-c", "merge.conflictStyle=merge", "merge-tree", "--write-tree", "--messages"]
    if base:
        args.append(f"--merge-base={base}")
    proc = git(repo, *args, a, b, ok=(0, 1))
    lines = proc.stdout.split("\n")
    tree = lines[0].strip()
    i, conflicted = 1, []
    while i < len(lines) and lines[i]:
        _, path = lines[i].split("\t", 1)
        if path not in conflicted:
            conflicted.append(path)
        i += 1
    conflicts: dict[str, Conflict] = {}
    for ln in lines[i + 1 :]:
        m = re.match(r"CONFLICT \(([^)]*)\): (.*)", ln)
        if not m:
            continue
        hit = next((p for p in conflicted if p in m.group(2)), None)
        if hit is None:
            continue
        if hit in conflicts:
            conflicts[hit].message += "; " + m.group(2)
        else:
            conflicts[hit] = Conflict(hit, m.group(1), m.group(2))
    for path in conflicted:
        conflicts.setdefault(path, Conflict(path, "unknown", "conflicted"))
    for c in conflicts.values():
        text = show(repo, tree, c.path)
        if text is not None:
            c.regions = conflict_regions(text)
    return tree, list(conflicts.values())


def conflict_regions(text: str) -> list[tuple[int, int]]:
    """1-based (first, last) line of each conflict hunk, markers included."""
    out, start = [], None
    for n, ln in enumerate(text.splitlines(), 1):
        if ln.startswith(MARK_OURS) and start is None:
            start = n
        elif ln.startswith(MARK_THEIRS) and start is not None:
            out.append((start, n))
            start = None
    return out


def resolve(text: str, side: str) -> tuple[str, list[int]]:
    """The file with every conflict hunk resolved to side 'A' (ours) or 'B' (theirs), and for each of its lines
    the 1-based line number in the merged file with markers."""
    keep, linemap, state = [], [], None
    for n, ln in enumerate(text.splitlines(), 1):
        if state is None and ln.startswith(MARK_OURS):
            state = "A"
            continue
        if state is not None:
            if ln.startswith(MARK_BASE) and state == "A":
                state = "base"
                continue
            if ln.strip() == MARK_SEP and ln.startswith(MARK_SEP) and state in ("A", "base"):
                state = "B"
                continue
            if ln.startswith(MARK_THEIRS) and state == "B":
                state = None
                continue
            if state != side:
                continue
        keep.append(ln)
        linemap.append(n)
    return "\n".join(keep) + "\n", linemap


def variants(text: str, conflicted: bool) -> list[tuple[str, str, list[int]]]:
    """(label, source, line map) of each parseable version of a merged file."""
    if not conflicted:
        n = text.count("\n") + 1
        return [("", text, list(range(1, n + 1)))]
    return [(f"hunks as {s}", *resolve(text, s)) for s in ("A", "B")]


# ---------------------------------------------------------------- definitions


Key = tuple[str, str, str]  # (kind, owner, name): kind function | class | member | param


@dataclass
class Def:
    path: str
    line: int
    what: str  # human kind: function, class, method, attribute, keyword argument of X()


def is_datalike(cls: ast.ClassDef) -> bool:
    for d in cls.decorator_list:
        d = d.func if isinstance(d, ast.Call) else d
        if (isinstance(d, ast.Name) and d.id == "dataclass") or (
            isinstance(d, ast.Attribute) and d.attr == "dataclass"
        ):
            return True
    for b in cls.bases:
        name = b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else None
        if name in DATA_BASES:
            return True
    return False


def definitions(src: str | None, path: str) -> dict[Key, Def]:
    """Module-level functions and classes, class members (methods, class variables, self.x assignments) and the
    keyword arguments of module and class functions."""
    out: dict[Key, Def] = {}
    if not src:
        return out
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out

    def add(key: Key, line: int, what: str) -> None:
        out.setdefault(key, Def(path, line, what))

    def visit_func(fn: ast.FunctionDef | ast.AsyncFunctionDef, cls: str | None) -> None:
        qual = f"{cls}.{fn.name}" if cls else fn.name
        if cls:
            prop = any(isinstance(d, ast.Name) and d.id in ("property", "cached_property") for d in fn.decorator_list)
            add(("member", cls, fn.name), fn.lineno, "property" if prop else "method")
        else:
            add(("function", "", fn.name), fn.lineno, "function")
        callee = cls if (cls and fn.name == "__init__") else fn.name
        for a in fn.args.args + fn.args.kwonlyargs:
            if a.arg not in SELF_NAMES:
                add(("param", qual, a.arg), a.lineno, f"keyword argument of {callee}()")
        if cls and fn.args.args:
            me = fn.args.args[0].arg
            for node in ast.walk(fn):
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target]
                    if isinstance(node, (ast.AnnAssign, ast.AugAssign))
                    else []
                )
                for t in targets:
                    for sub in ast.walk(t):
                        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == me:
                            add(("member", cls, sub.attr), sub.lineno, "attribute")

    def visit_class(cls: ast.ClassDef) -> None:
        add(("class", "", cls.name), cls.lineno, "class")
        has_init = any(
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "__init__" for n in cls.body
        )
        datalike = is_datalike(cls) and not has_init
        for n in cls.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit_func(n, cls.name)
            elif isinstance(n, ast.ClassDef):
                visit_class(n)
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        add(("member", cls.name, t.id), n.lineno, "attribute")
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                add(("member", cls.name, n.target.id), n.lineno, "attribute")
                if datalike:
                    add(("param", f"{cls.name}.__init__", n.target.id), n.lineno, f"keyword argument of {cls.name}()")

    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visit_func(n, None)
        elif isinstance(n, ast.ClassDef):
            visit_class(n)
    return out


def is_public(key: Key) -> bool:
    kind, owner, name = key
    if name.startswith("_"):
        return False
    if kind == "param":
        fn = owner.split(".")[-1]
        return fn == "__init__" or not fn.startswith("_")
    return True


def drop_children(removed: dict[Key, Def]) -> dict[Key, Def]:
    """A removed class or function is reported once, not again for each of its members and arguments."""
    gone_cls = {k[2] for k in removed if k[0] == "class"}
    gone_fn = {k[2] for k in removed if k[0] == "function"} | {f"{k[1]}.{k[2]}" for k in removed if k[0] == "member"}
    out = {}
    for k, v in removed.items():
        if k[0] == "member" and k[1] in gone_cls:
            continue
        if k[0] == "param" and (k[1] in gone_fn or k[1].split(".")[0] in gone_cls and "." in k[1]):
            continue
        out[k] = v
    return out


# ---------------------------------------------------------------- references in the merged tree


@dataclass
class Ref:
    path: str
    line: int
    text: str
    how: str
    in_conflict: bool


@dataclass
class Removed:
    name: str
    what: str
    owners: list[str]
    side: str
    head: str
    defined_at: str
    renamed_to: list[str]
    keys: list[Key]
    refs: list[Ref] = field(default_factory=list)


class Uses(ast.NodeVisitor):
    """Every use of a name in one source: Name loads, attribute loads, `from x import name`, call keywords."""

    def __init__(self) -> None:
        self.names: list[tuple[str, int]] = []
        self.attrs: list[tuple[str, int, str | None]] = []  # (attr, line, enclosing class when on self/cls)
        self.imports: list[tuple[str, int]] = []
        self.keywords: list[tuple[str | None, str, int]] = []  # (callee last name, keyword, line)
        self.bound: set[str] = set()
        self.members: dict[str, set[str]] = {}
        self._cls: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bound.add(node.name)
        self.members[node.name] = class_members(node)
        self._cls.append(node.name)
        self.generic_visit(node)
        self._cls.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.bound.add(node.name)
        a = node.args
        for arg in a.posonlyargs + a.args + a.kwonlyargs + [x for x in (a.vararg, a.kwarg) if x]:
            self.bound.add(arg.arg)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.names.append((node.id, node.lineno))
        else:
            self.bound.add(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, (ast.Load, ast.Del)):
            on_self = isinstance(node.value, ast.Name) and node.value.id in SELF_NAMES
            self.attrs.append((node.attr, node.lineno, self._cls[-1] if (on_self and self._cls) else None))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self.bound.add(a.asname or a.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for a in node.names:
            self.imports.append((a.name, node.lineno))

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        callee = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
        for kw in node.keywords:
            if kw.arg:
                self.keywords.append((callee, kw.arg, kw.value.lineno if hasattr(kw.value, "lineno") else node.lineno))
        self.generic_visit(node)


def class_members(cls: ast.ClassDef) -> set[str]:
    """Methods, class variables and self.x assignments of one class."""
    out = set()
    for n in cls.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(n.name)
            me = n.args.args[0].arg if n.args.args else None
            for node in ast.walk(n):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.ctx, ast.Store)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == me
                ):
                    out.add(node.attr)
        elif isinstance(n, ast.Assign):
            out |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
    return out


def find_refs(group: Removed, uses: Uses) -> list[tuple[int, str]]:
    """(line in the parsed source, how) of each use of a removed name."""
    hits: list[tuple[int, str]] = []
    kinds = {k[0] for k in group.keys}
    name = group.name
    if kinds & {"function", "class"}:
        if name not in uses.bound:
            hits += [(ln, "name") for n, ln in uses.names if n == name]
        hits += [(ln, "import") for n, ln in uses.imports if n == name]
        hits += [(ln, "attribute") for n, ln, _ in uses.attrs if n == name]
    if "member" in kinds:
        for n, ln, cls in uses.attrs:
            if n == name and not (cls and name in uses.members.get(cls, set())):
                hits.append((ln, "attribute"))
    if "param" in kinds:
        callees = set()
        for k in group.keys:
            parts = k[1].split(".")
            callees.add(parts[0] if parts[-1] == "__init__" else parts[-1])
        hits += [(ln, "keyword") for callee, kw, ln in uses.keywords if kw == name and callee in callees]
    return sorted(set(hits))


def side_lines(repo: str, rev: str, path: str, cache: dict) -> set[str]:
    key = (rev, path)
    if key not in cache:
        text = show(repo, rev, path)
        cache[key] = {ln.strip() for ln in text.splitlines()} if text else set()
    return cache[key]


# ---------------------------------------------------------------- ruff


LINT_CODES = "F821,F841,F811,F401"
PYFLAKES_CODES = {
    "undefined name": "F821",
    "assigned to but never used": "F841",
    "redefinition of unused": "F811",
    "imported but unused": "F401",
}


def pixi_manifest(repo: str) -> Path | None:
    root = Path(repo)
    if (root / "pixi.toml").exists():
        return root / "pixi.toml"
    pyproject = root / "pyproject.toml"
    if pyproject.exists() and "[tool.pixi" in pyproject.read_text(errors="replace"):
        return pyproject
    return None


def find_linter(repo: str) -> list[str] | None:
    """The repo's own ruff first (the version its lint runs), then any other ruff, then pyflakes."""
    env = os.environ.get("MERGE_NAMES_RUFF")
    if env:
        return [env]
    for env_name in ("lint", "dev", "default", "test"):
        p = Path(repo) / ".pixi" / "envs" / env_name / "bin" / "ruff"
        if p.exists():
            return [str(p)]
    manifest = pixi_manifest(repo)
    if manifest is not None and shutil.which("pixi"):
        cmd = ["pixi", "run", "--manifest-path", str(manifest), "-e", "lint", "ruff"]
        probe = subprocess.run([*cmd, "--version"], check=False, capture_output=True)
        if probe.returncode == 0:
            return cmd
    found = shutil.which("ruff")
    if found:
        return [found]
    if shutil.which("uvx"):
        probe = subprocess.run(["uvx", "ruff", "--version"], check=False, capture_output=True)
        if probe.returncode == 0:
            return ["uvx", "ruff"]
    probe = subprocess.run([sys.executable, "-m", "pyflakes", "--version"], check=False, capture_output=True)
    if probe.returncode == 0:
        return ["pyflakes"]
    return None


def lint(linter: list[str], root: Path) -> list[tuple[str, int, str, str]]:
    """(relative path, line, code, message) of every LINT_CODES finding under root."""
    if not any(root.rglob("*.py")):
        return []
    out = []
    if linter == ["pyflakes"]:
        proc = subprocess.run(
            [sys.executable, "-m", "pyflakes", str(root)], check=False, capture_output=True, text=True
        )
        for ln in proc.stdout.splitlines():
            m = re.match(r"(.+?):(\d+):(?:\d+:)? (.*)", ln)
            if not m:
                continue
            msg = m.group(3)
            code = next((c for k, c in PYFLAKES_CODES.items() if k in msg), None)
            if code:
                out.append((str(Path(m.group(1)).relative_to(root)), int(m.group(2)), code, msg))
        return out
    cmd = [*linter, "check", "--isolated", "--no-cache", "--no-fix", "--select", LINT_CODES]
    cmd += ["--output-format", "json", str(root)]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    try:
        items = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise GitError(f"ruff output unreadable: {proc.stderr.strip() or exc}") from exc
    for it in items:
        code = it.get("code") or "syntax-error"
        rel = str(Path(it["filename"]).resolve().relative_to(root.resolve()))
        out.append((rel, it["location"]["row"], code, it["message"]))
    return out


# ---------------------------------------------------------------- the check


def changed_py(repo: str, base: str, head: str) -> tuple[list[str], list[str]]:
    """(paths at base, paths at head) of the .py files base..head changed, renames followed."""
    out = git(repo, "diff", "-z", "--name-status", "-M", base, head, "--", "*.py").stdout.split("\0")
    old, new, i = [], [], 0
    while i < len(out) and out[i]:
        st = out[i][0]
        if st in "RC":
            if st == "R":
                old.append(out[i + 1])
            new.append(out[i + 2])
            i += 3
            continue
        path = out[i + 1]
        if st in "MDT":
            old.append(path)
        if st in "AMT":
            new.append(path)
        i += 2
    return old, new


def check(repo: str, head_a: str, head_b: str, base_ref: str | None) -> dict:
    sha = {
        s: git(repo, "rev-parse", "--verify", f"{r}^{{commit}}").stdout.strip()
        for s, r in (("A", head_a), ("B", head_b))
    }
    if base_ref:
        base = git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}").stdout.strip()
    else:
        base = git(repo, "merge-base", sha["A"], sha["B"]).stdout.split()[0]
    tree, conflicts = merge_tree(repo, sha["A"], sha["B"], base if base_ref else None)
    conflicted = {c.path: c for c in conflicts}

    changed = {s: changed_py(repo, base, sha[s]) for s in ("A", "B")}
    merged_cache: dict[str, list[tuple[str, str, list[int]]] | None] = {}

    def merged_variants(path: str) -> list[tuple[str, str, list[int]]]:
        if path not in merged_cache:
            text = show(repo, tree, path)
            merged_cache[path] = None if text is None else variants(text, path in conflicted)
        return merged_cache[path] or []

    def merged_text(path: str) -> list[str]:
        return (show(repo, tree, path) or "").splitlines()

    # (b) names removed by one side, still referenced in the merged tree
    all_paths = sorted({p for s in changed.values() for lst in s for p in lst})
    merged_defs: dict[Key, Def] = {}
    for p in all_paths:
        for _, src, _ in merged_variants(p):
            for k, v in definitions(src, p).items():
                merged_defs.setdefault(k, v)

    groups: list[Removed] = []
    for s in ("A", "B"):
        old, new = changed[s]
        base_defs: dict[Key, Def] = {}
        for p in old:
            base_defs.update({k: v for k, v in definitions(show(repo, base, p), p).items() if k not in base_defs})
        side_defs: dict[Key, Def] = {}
        for p in new:
            side_defs.update(definitions(show(repo, sha[s], p), p))
        removed = {k: v for k, v in base_defs.items() if k not in side_defs and is_public(k) and k not in merged_defs}
        removed = drop_children(removed)
        added = [k for k in side_defs if k not in base_defs]
        by_name: dict[tuple[str, str], list[Key]] = {}
        for k in removed:
            # attributes group by name over all owners (the use site cannot tell the class); arguments by callee
            gk = ("member", k[2]) if k[0] == "member" else (k[0], k[1] + "." + k[2])
            by_name.setdefault(gk, []).append(k)
        for keys in by_name.values():
            k0 = keys[0]
            d = removed[k0]
            owners_k = {k[1] for k in keys}
            similar = sorted({a[2] for a in added if a[0] == k0[0] and a[1] in owners_k})
            hint = difflib.get_close_matches(k0[2], similar, n=3, cutoff=0.6)
            owners = sorted({k[1].split(".")[0] if k[0] == "param" else k[1] for k in keys if k[1]})
            what = d.what if k0[0] != "member" else ("method" if d.what in ("method", "property") else "attribute")
            groups.append(Removed(k0[2], what, owners, s, sha[s][:9], f"{d.path}:{d.line}", hint, keys))

    if groups:
        names = sorted({g.name for g in groups})
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("\n".join(names) + "\n")
            pattern_file = fh.name
        try:
            proc = git(repo, "grep", "-l", "-F", "-w", "-f", pattern_file, tree, "--", "*.py", ok=(0, 1))
        finally:
            os.unlink(pattern_file)
        candidates = [ln.split(":", 1)[1] for ln in proc.stdout.splitlines() if ":" in ln]
        line_cache: dict = {}
        for path in candidates:
            parsed = []
            for _label, src, linemap in merged_variants(path):
                try:
                    tree_ast = ast.parse(src)
                except SyntaxError:
                    continue
                uses = Uses()
                uses.visit(tree_ast)
                parsed.append((uses, linemap))
            if not parsed:
                continue
            text = merged_text(path)
            joined = "\n".join(text)
            regions = conflicted[path].regions if path in conflicted else []
            for g in groups:
                if g.name not in joined:
                    continue
                own = side_lines(repo, sha[g.side], path, line_cache)
                seen = {(r.path, r.line) for r in g.refs}
                for uses, linemap in parsed:
                    for ln, how in find_refs(g, uses):
                        mline = linemap[ln - 1] if ln - 1 < len(linemap) else ln
                        src_line = text[mline - 1] if mline - 1 < len(text) else ""
                        if (path, mline) in seen or src_line.strip() in own:
                            continue
                        seen.add((path, mline))
                        inside = any(a <= mline <= b for a, b in regions)
                        g.refs.append(Ref(path, mline, src_line.strip(), how, inside))
        for g in groups:
            g.refs.sort(key=lambda r: (r.path, r.line))
    stale = [g for g in groups if g.refs]
    stale.sort(key=lambda g: (g.side, g.name, g.what))

    # (c) undefined names, unused locals, redefinitions and unused imports in the merged changed files
    linter = find_linter(repo)
    undefined, lint_note = [], ""
    py_merged = sorted({p for s in changed.values() for p in s[1]})
    if linter is None:
        lint_note = "skipped: no ruff or pyflakes found"
    elif py_merged:
        with tempfile.TemporaryDirectory(prefix="merge_names_") as tmp:
            roots = {r: Path(tmp) / r for r in ("A", "B", "merged A", "merged B")}
            maps: dict[tuple[str, str], list[int]] = {}
            sources: dict[tuple[str, str], list[str]] = {}

            def put(root: str, path: str, src: str) -> None:
                (roots[root] / path).parent.mkdir(parents=True, exist_ok=True)
                (roots[root] / path).write_text(src)
                sources[(root, path)] = src.splitlines()

            for p in py_merged:
                for s in ("A", "B"):
                    src = show(repo, sha[s], p)
                    if src is not None:
                        put(s, p, src)
                for label, src, linemap in merged_variants(p):
                    which = "merged B" if label.endswith("B") else "merged A"
                    put(which, p, src)
                    maps[(which, p)] = linemap

            def keyed(root: str) -> list[tuple[str, int, str, str, str]]:
                """Findings with the stripped text of their line, which survives line shifts between trees."""
                out = []
                for p, row, code, msg in lint(linter, roots[root]):
                    lines = sources.get((root, p), [])
                    out.append((p, row, code, msg, lines[row - 1].strip() if 0 < row <= len(lines) else ""))
                return out

            in_head = {s: {(p, code, msg, text) for p, _, code, msg, text in keyed(s)} for s in ("A", "B")}
            hits: dict[tuple[str, int, str, str], tuple[list[str], str]] = {}
            for which in ("merged A", "merged B"):
                for p, row, code, msg, text in keyed(which):
                    linemap = maps.get((which, p), [])
                    mline = linemap[row - 1] if 0 < row <= len(linemap) else row
                    sides, _ = hits.setdefault((p, mline, code, msg), ([], text))
                    if which[-1] not in sides:
                        sides.append(which[-1])
            for (p, mline, code, msg), (sides, text) in hits.items():
                variant = ""
                if p in conflicted:
                    variant = (
                        "with either hunk resolution" if len(sides) == 2 else f"only with hunks taken from {sides[0]}"
                    )
                undefined.append(
                    {
                        "path": p,
                        "line": mline,
                        "code": code,
                        "message": msg,
                        "variant": variant,
                        "pre_existing": (p, code, msg, text) in in_head["A"],
                        "in_b": (p, code, msg, text) in in_head["B"],
                    }
                )
        # a finding that depends on how a conflict is resolved is listed after the ones that hold either way
        undefined.sort(key=lambda u: (u["pre_existing"], u["variant"].startswith("only"), u["path"], u["line"]))
        lint_note = f"{' '.join(linter)} on {len(py_merged)} files"

    new_undefined = [u for u in undefined if not u["pre_existing"]]
    findings = len(conflicts) + len(stale) + len(new_undefined)
    return {
        "repo": repo,
        "a": {"ref": head_a, "sha": sha["A"]},
        "b": {"ref": head_b, "sha": sha["B"]},
        "base": base,
        "merged_tree": tree,
        "conflicts": [c.__dict__ for c in conflicts],
        "removed_names_still_used": [
            {**{k: v for k, v in g.__dict__.items() if k not in ("refs", "keys")}, "refs": [r.__dict__ for r in g.refs]}
            for g in stale
        ],
        "undefined": undefined,
        "lint": lint_note,
        "findings": findings,
    }


def render(res: dict) -> str:
    a, b = res["a"], res["b"]
    head = (
        f"merge_names: A {a['ref']} ({a['sha'][:9]}) x B {b['ref']} ({b['sha'][:9]}), "
        f"base {res['base'][:9]}, merged tree {res['merged_tree'][:9]}"
    )
    out = [
        head,
        "",
        f"(a) textual conflicts: {len(res['conflicts'])}",
    ]
    for c in res["conflicts"]:
        regs = ", ".join(f"{s}-{e}" for s, e in c["regions"]) or "-"
        out.append(f"  {c['path']}  lines {regs} ({c['kind']})")
    stale = res["removed_names_still_used"]
    out += ["", f"(b) names one side removed that the merged tree still uses: {len(stale)}"]
    for g in stale:
        owner = f" of {', '.join(g['owners'])}" if g["owners"] and not g["what"].startswith("keyword") else ""
        hint = f"; renamed to {', '.join(g['renamed_to'])}?" if g["renamed_to"] else ""
        out.append(
            f"  {g['name']}: {g['what']}{owner}, removed by {g['side']} ({g['head']}) at {g['defined_at']}{hint}"
        )
        for r in g["refs"]:
            tag = "  [in conflict hunk]" if r["in_conflict"] else ""
            out.append(f"    {r['path']}:{r['line']}  {r['text']}{tag}")
    und = res["undefined"]
    new = [u for u in und if not u["pre_existing"]]
    n_pre = len(und) - len(new)
    out += [
        "",
        (
            f"(c) undefined, unused or redefined names in the merged files: {len(new)} new, {n_pre} already in A"
            f" ({res['lint']})"
        ),
    ]
    for u in und:
        flags = (
            "also in B" if u.get("in_b") and not u["pre_existing"] else "",
            "already in A" if u["pre_existing"] else "",
        )
        tags = [t for t in (u["variant"], *flags) if t]
        tag = f"  [{'; '.join(tags)}]" if tags else ""
        out.append(f"  {u['path']}:{u['line']}  {u['code']} {u['message']}{tag}")
    verdict = "clean" if res["findings"] == 0 else f"{res['findings']} findings"
    out += ["", f"result: {verdict}"]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repo", help="path of the git repository (any worktree)")
    ap.add_argument("head_a", help="first head (ours: the conflict markers show it first)")
    ap.add_argument("head_b", help="second head")
    ap.add_argument("--base", help="merge base to use instead of `git merge-base A B`")
    ap.add_argument("--json", action="store_true", help="print the structured result")
    ap.add_argument("--out", type=Path, help="also write merge_names.txt and merge_names.json into this folder")
    ap.add_argument("--exit-zero", action="store_true", help="exit 0 even with findings (still 2 on errors)")
    args = ap.parse_args(argv)
    try:
        res = check(args.repo, args.head_a, args.head_b, args.base)
    except GitError as exc:
        print(f"merge_names: {exc}", file=sys.stderr)
        return 2
    text, as_json = render(res), json.dumps(res, indent=1)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "merge_names.txt").write_text(text + "\n")
        (args.out / "merge_names.json").write_text(as_json + "\n")
    print(as_json if args.json else text)
    return 1 if res["findings"] and not args.exit_zero else 0


if __name__ == "__main__":
    sys.exit(main())
