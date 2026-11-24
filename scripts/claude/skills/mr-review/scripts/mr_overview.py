#!/usr/bin/env python3
"""The MR architecture overview (references/mr-overview.md): facts from git and the code, the worker's notes, drawings.

Five levels, one renderer: (1) repo map, (2) data flow, (3) pipeline place, (4) class and function cards,
(5) before vs after. The script makes the facts (files, symbols and their status, signatures, docstring argument
lines, call sites, thread anchors) so they are exact; the worker writes the notes (what the MR does, one
description per box, the data flow, before vs after); `check` ties the two.

Usage:
    mr_overview.py facts REPO --base SHA --head SHA --out FACTS.json
    mr_overview.py build DIR --facts FACTS.json --notes NOTES.json [--threads THREADS.json] [--repo REPO]
                     [--project product/site3d]
    mr_overview.py check DIR --head SHA            exit 1 on a note naming an unknown box, an undescribed box, a
                     changed symbol without before vs after, a data-flow edge that disagrees with a signature, or
                     a page or drawing with a script, a handler or an outside link
    mr_overview.py make REPO --base SHA --head SHA --notes NOTES.json [--threads THREADS.json] --out DIR
                     facts, build and check in one call (exit 1 when check fails; the files are written anyway)
    mr_overview.py embed DIR --head SHA --into PAGE.html
                     put (or replace) the overview fragment under the page's first <h1> (the review briefing)

Files in DIR (<head12> = the first 12 hex digits of the MR head):
    overview-<head12>.html   the five levels, self-contained, no script (a light panel; reads the same on a dark page)
    overview-<head12>.json   facts, notes, boxes, anchors, check result: the full record
    overview-<head12>.txt    the overview's words, for the cold reader
    overview-<head12>-map.svg, -flow.svg, -pipe.svg, -mini-<disc8>.svg (one per thread that lands on a box)

THREADS.json is `mr_discussions.py --all --json`: each thread's `id`, GitLab `position` and `notes`.
Standard library only, so any python3 runs it.
"""

from __future__ import annotations

import argparse
import ast
import copy
import datetime as dt
import html
import json
import re
import subprocess
import sys
from pathlib import Path

HOST = "gitlab.gc.arraylabs.net"
esc = html.escape
HEX40 = re.compile(r"^[0-9a-f]{40}$")

# The site3d pipeline (site3d CLAUDE.md, "Core Processing Pipeline"); sar_simulation feeds image formation.
PIPE = [
    ("raw", "Raw SAR data", "CPHD, SICD, SIDD files"),
    ("sar_common", "sar_common", "readers, geometry, motion"),
    ("image_formation", "image_formation", "backprojection, PFA"),
    ("reconstruction", "reconstruction", "height and 3-D from many images"),
    ("visualization_exports", "visualization_exports", "3D Tiles, GLB"),
]
STAGES = [k for k, _, _ in PIPE] + ["sar_simulation"]
SYMBOL_KINDS = ("class", "function", "method")
START, END = "<!-- mr-overview:start -->", "<!-- mr-overview:end -->"


# --- git ---


def git(repo: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=False)
    if r.returncode:
        raise SystemExit(f"git {' '.join(args)} in {repo}: {r.stderr.strip()}")
    return r.stdout


def has_commit(repo: str, sha: str) -> bool:
    r = subprocess.run(["git", "-C", repo, "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True, check=False)
    return r.returncode == 0


def require_commit(repo: str, sha: str) -> None:
    if not has_commit(repo, sha):
        raise SystemExit(f"{sha} is not in {repo}: fetch it first (git -C {repo} fetch origin)")


def show(repo: str, sha: str, path: str) -> str | None:
    r = subprocess.run(["git", "-C", repo, "show", f"{sha}:{path}"], capture_output=True, text=True, check=False)
    return r.stdout if r.returncode == 0 else None


def name_status(repo: str, mb: str, head: str) -> list[tuple[str, str, str]]:
    """(status letter, old path, new path) per file, renames followed (-M)."""
    parts = git(repo, "diff", "--name-status", "-z", "-M", mb, head).split("\0")
    out, i = [], 0
    while i < len(parts) and parts[i]:
        st = parts[i][0]
        if st in "RC":
            out.append((st, parts[i + 1], parts[i + 2]))
            i += 3
        else:
            out.append((st, parts[i + 1], parts[i + 1]))
            i += 2
    return out


def numstat(repo: str, mb: str, head: str) -> dict[str, tuple[int, int]]:
    """Added and removed lines per new path (binary files count 0)."""
    parts = git(repo, "diff", "--numstat", "-z", "-M", mb, head).split("\0")
    out, i = {}, 0
    while i < len(parts) and parts[i]:
        a, b, p = parts[i].split("\t", 2)
        if p == "":  # a rename: the two paths follow
            p = parts[i + 2]
            i += 3
        else:
            i += 1
        out[p] = (int(a) if a != "-" else 0, int(b) if b != "-" else 0)
    return out


def hunks(repo: str, mb: str, head: str, old: str, new: str) -> list[tuple[int, int, int, int]]:
    """(old_start, old_len, new_start, new_len) of each zero-context hunk, a rename followed."""
    paths = [old] if old == new else [old, new]
    out = []
    for line in git(repo, "diff", "-U0", "-M", mb, head, "--", *paths).splitlines():
        m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if m:
            a, b, c, d = m.groups()
            out.append((int(a), int(b if b is not None else 1), int(c), int(d if d is not None else 1)))
    return out


# --- symbols ---


def parse(src: str | None) -> ast.Module | None:
    if src is None:
        return None
    try:
        return ast.parse(src)
    except SyntaxError:
        return None


def symbols_of(src: str | None) -> dict[str, dict]:
    """Top-level classes and functions and the methods of classes: qualname -> kind, span (decorators in), node."""
    tree = parse(src)
    out: dict[str, dict] = {}
    if tree is None:
        return out
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([n.lineno] + [d.lineno for d in n.decorator_list])
            out[n.name] = {"kind": "class" if isinstance(n, ast.ClassDef) else "function", "from": start,
                           "to": n.end_lineno, "node": n, "def_line": n.lineno}
            if isinstance(n, ast.ClassDef):
                for m in n.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        ms = min([m.lineno] + [d.lineno for d in m.decorator_list])
                        out[f"{n.name}.{m.name}"] = {"kind": "method", "from": ms, "to": m.end_lineno, "node": m,
                                                     "def_line": m.lineno}
    return out


def doc_args(doc: str | None) -> dict[str, str]:
    """Argument and attribute lines of a Google ("Args:", "Attributes:") or numpy ("Parameters") docstring."""
    if not doc:
        return {}
    out: dict[str, str] = {}
    lines = doc.splitlines()
    i, mode = 0, None
    while i < len(lines):
        s = lines[i].strip()
        if s in ("Args:", "Attributes:", "Parameters"):
            mode = "g" if s.endswith(":") else "n"
            i += 2 if mode == "n" else 1
            continue
        if s in ("Returns:", "Raises:", "Returns", "Example:", "Examples:", "Raises") or (mode == "n" and set(s) == {"-"}):
            mode = None
        if mode == "g":
            m = re.match(r"(\w+)(?: \([^)]*\))?: (.*)", s)
            if m:
                txt, ind = m.group(2), len(lines[i]) - len(lines[i].lstrip())
                j = i + 1
                # continuation lines are indented deeper than the argument's own line
                while j < len(lines) and lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) > ind:
                    txt += " " + lines[j].strip()
                    j += 1
                out[m.group(1)] = txt
        elif mode == "n":
            m = re.match(r"(\w+) : (.*)", s)
            if m and i + 1 < len(lines):
                txt, j = lines[i + 1].strip(), i + 2
                while j < len(lines) and lines[j].startswith("    ") and lines[j].strip():
                    txt += " " + lines[j].strip()
                    j += 1
                out[m.group(1)] = txt
        i += 1
    return out


def _params(node: ast.AST) -> list[tuple[ast.arg, ast.AST | None]]:
    a = node.args
    pos = a.posonlyargs + a.args
    dflt = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    return list(zip(pos, dflt)) + list(zip(a.kwonlyargs, a.kw_defaults))


def signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        return f"class {node.name}"
    a = node.args
    n_pos = len(a.posonlyargs + a.args)
    parts = []
    for i, (arg, d) in enumerate(_params(node)):
        if i == n_pos and a.vararg is None and a.kwonlyargs:
            parts.append("*")
        if i == n_pos and a.vararg is not None:
            parts.append("*" + a.vararg.arg)
        s = arg.arg + (f": {ast.unparse(arg.annotation)}" if arg.annotation else "")
        parts.append(s + (f" = {ast.unparse(d)}" if d is not None else ""))
    if a.vararg is not None and not a.kwonlyargs:
        parts.append("*" + a.vararg.arg)
    if a.kwarg is not None:
        parts.append("**" + a.kwarg.arg)
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"def {node.name}({', '.join(parts)}){ret}"


def fields(node: ast.AST) -> list[dict]:
    """Dataclass fields (annotated assignments in the class body) or the parameters of a function."""
    if isinstance(node, ast.ClassDef):
        return [{"name": s.target.id, "type": ast.unparse(s.annotation),
                 "default": ast.unparse(s.value) if s.value is not None else ""}
                for s in node.body if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)]
    return [{"name": x.arg, "type": ast.unparse(x.annotation) if x.annotation else "",
             "default": ast.unparse(d) if d is not None else ""} for x, d in _params(node) if x.arg not in ("self", "cls")]


def _strip_doc(node: ast.AST) -> str:
    """The node's code without docstrings (of itself and of anything nested), to tell a docstring-only change."""
    n = copy.deepcopy(node)
    for x in ast.walk(n):
        body = getattr(x, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str)):
            x.body = body[1:] or [ast.Pass()]
    return ast.dump(n)


def _overlap(lo: int, n: int, a: int, b: int) -> int:
    """Lines of the hunk side [lo, lo + n) that fall in [a, b]."""
    return max(0, min(lo + n - 1, b) - max(lo, a) + 1)


def _class_own_minus(old: dict, q: str, hk) -> bool:
    """A removed line of class q's old body that sits outside all of its methods."""
    o = old[q]
    meth = [old[x] for x in old if x.startswith(q + ".")]
    for a, b, _, _ in hk:
        for ln in range(a, a + b):
            if o["from"] <= ln <= o["to"] and not any(m["from"] <= ln <= m["to"] for m in meth):
                return True
    return False


def is_test(path: str) -> bool:
    return "/tests/" in path or path.startswith("tests/")


def _symbols_of_file(new_src, old_src, hk) -> list[dict]:
    new, old = symbols_of(new_src), symbols_of(old_src)
    out = []
    for q, s in new.items():
        plus = sum(_overlap(c, d, s["from"], s["to"]) for _, _, c, d in hk if d)
        minus = 0
        if q in old:
            o = old[q]
            minus = sum(_overlap(a, b, o["from"], o["to"]) for a, b, _, _ in hk if b)
            # a pure deletion sits after new line c: count it when that point is inside the symbol
            if not plus and not minus:
                minus = sum(1 for a, b, c, d in hk if not d and s["from"] <= c < s["to"])
        status = "added" if q not in old else ("changed" if plus or minus else "unchanged")
        if status == "changed" and _strip_doc(s["node"]) == _strip_doc(old[q]["node"]):
            status = "doc-only"
        if s["kind"] == "class" and status == "changed":
            # a class is changed by its own lines (fields, docstring), not only through its methods
            own = [x for x in new if x.startswith(q + ".")]
            in_methods = sum(_overlap(c, d, new[x]["from"], new[x]["to"]) for x in own for _, _, c, d in hk if d)
            if plus - in_methods <= 0 and not (minus and _class_own_minus(old, q, hk)):
                status = "via-methods"
        node = s["node"]
        doc = ast.get_docstring(node)
        out.append({
            "qualname": q, "kind": s["kind"], "status": status, "line": s["def_line"], "line_to": s["to"],
            "plus": plus, "minus": minus, "signature": signature(node),
            "signature_before": signature(old[q]["node"]) if q in old else None,
            "returns": ast.unparse(node.returns) if getattr(node, "returns", None) else "",
            "fields": fields(node), "fields_before": fields(old[q]["node"]) if q in old else None,
            "doc_first": (doc or "").split("\n\n")[0].replace("\n", " "), "doc_args": doc_args(doc),
        })
    for q in old:
        if q not in new:
            out.append({"qualname": q, "kind": old[q]["kind"], "status": "removed", "line": None,
                        "plus": 0, "minus": old[q]["to"] - old[q]["from"] + 1})
    return out


# --- call sites, resolved through ast ---
#
# A reference is a tuple: ("module", path) a repo module; ("pkg", dir) a repo folder imported as a package;
# ("sym", path, qualname) something a repo module defines (a function, a class, a method, a field, a variable);
# ("ext", dotted) anything outside the repo (numpy, builtins, typing). None: not knowable from the code.
# An instance's type is the reference of its class.

PENDING, PENDING_MISS = object(), object()
DEPTH = 12  # how far a chain of imports, annotations and assignments is followed before giving up


class Mod:
    """One parsed module at the head and its module scope."""

    def __init__(self, path: str, src: str):
        self.path = path
        self.dir = str(Path(path).parent) if "/" in path else ""
        self.lines = src.splitlines()
        self.tree = parse(src)
        self.star = False  # a `from x import *`: a bare name may come from anywhere
        self.scope: Scope | None = None


class Scope:
    """The names a function (or the module) binds, and how: ("ann", annotation), ("value", expression),
    ("import", module, name or None, level), ("def", qualname) a module-level def or class, ("self", class),
    ("cls", class), ("unknown",). Flow-insensitive: every binding anywhere in the body counts."""

    def __init__(self, mod: Mod, parent: Scope | None, owner: tuple | None = None):
        self.mod, self.parent, self.owner = mod, parent, owner
        self.binds: dict[str, list[tuple]] = {}

    def add(self, name: str, how: tuple) -> None:
        self.binds.setdefault(name, []).append(how)


def is_property(node: ast.AST | None) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for d in node.decorator_list:
        nm = d.id if isinstance(d, ast.Name) else d.attr if isinstance(d, ast.Attribute) else ""
        if nm in ("property", "cached_property"):
            return True
    return False


def _decorated(node: ast.AST, name: str) -> bool:
    return any((isinstance(d, ast.Name) and d.id == name) or (isinstance(d, ast.Attribute) and d.attr == name)
               for d in getattr(node, "decorator_list", []))


def _local_nodes(nodes: list[ast.AST]):
    """The nodes of a body that run in its own scope: nested defs, classes and lambdas are not entered."""
    stack = list(nodes)
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(n))


def _names_in(target: ast.AST) -> list[str]:
    return [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]


def _collect(scope: Scope, body: list[ast.AST], module_level: bool) -> None:
    gl: set[str] = set()
    for n in _local_nodes(body):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope.add(n.name, ("def", n.name) if module_level else ("unknown",))
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and len(n.targets) == 1:
                    scope.add(t.id, ("value", n.value))
                else:
                    for nm in _names_in(t) if not isinstance(t, ast.Attribute) else []:
                        scope.add(nm, ("unknown",))
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            scope.add(n.target.id, ("ann", n.annotation))
        elif isinstance(n, ast.NamedExpr):
            scope.add(n.target.id, ("value", n.value))
        elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
            for nm in _names_in(n.target):
                scope.add(nm, ("unknown",))
        elif isinstance(n, ast.withitem) and n.optional_vars is not None:
            for nm in _names_in(n.optional_vars):
                scope.add(nm, ("unknown",))
        elif isinstance(n, ast.ExceptHandler) and n.name:
            scope.add(n.name, ("unknown",))
        elif isinstance(n, ast.Lambda):
            for a in n.args.posonlyargs + n.args.args + n.args.kwonlyargs:
                scope.add(a.arg, ("unknown",))
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.asname:
                    scope.add(a.asname, ("import", a.name, None, 0))
                else:
                    top = a.name.split(".")[0]
                    scope.add(top, ("import", top, None, 0))
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name == "*":
                    scope.mod.star = True
                else:
                    scope.add(a.asname or a.name, ("import", n.module or "", a.name, n.level))
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            gl.update(n.names)
    for nm in gl:
        scope.binds.pop(nm, None)


class Code:
    """The repo at the head, read on demand: which name in which file refers to which definition."""

    def __init__(self, repo: str, head: str):
        self.repo, self.head = repo, head
        self.paths = {p for p in git(repo, "ls-tree", "-r", "-z", "--name-only", head).split("\0") if p}
        self.dirs = {str(d) for p in self.paths for d in Path(p).parents if str(d) != "."}
        self._mods: dict[str, Mod | None] = {}
        self._fscopes: dict[int, Scope] = {}
        self._members: dict[tuple, dict] = {}
        self._scoped: dict[str, list] = {}
        self._types: dict[tuple, object] = {}  # memo of name and field types; PENDING while one is worked out
        self._spans: dict[str, list[tuple[int, int, str]]] = {}

    # modules and scopes

    def mod(self, path: str) -> Mod | None:
        if path not in self._mods:
            src = show(self.repo, self.head, path) if path in self.paths else None
            m = Mod(path, src) if src is not None else None
            if m is not None and m.tree is not None:
                m.scope = Scope(m, None)
                _collect(m.scope, m.tree.body, True)
            self._mods[path] = m
        return self._mods[path]

    def func_scope(self, mod: Mod, fn: ast.AST, parent: Scope, owner: tuple | None) -> Scope:
        """`owner`: the class `fn` is a method of (its first parameter is then self, or cls for a classmethod)."""
        key = id(fn)
        if key not in self._fscopes:
            sc = Scope(mod, parent, owner)
            a = fn.args
            pos = a.posonlyargs + a.args
            for i, x in enumerate(pos + a.kwonlyargs):
                if i == 0 and owner is not None and x in pos and not _decorated(fn, "staticmethod"):
                    sc.add(x.arg, ("cls", owner) if _decorated(fn, "classmethod") else ("self", owner))
                elif x.annotation is not None:
                    sc.add(x.arg, ("ann", x.annotation))
                else:
                    sc.add(x.arg, ("unknown",))
            for x in (a.vararg, a.kwarg):
                if x is not None:
                    sc.add(x.arg, ("unknown",))
            _collect(sc, fn.body, False)
            self._fscopes[key] = sc
        return self._fscopes[key]

    def scoped(self, mod: Mod) -> list[tuple[ast.AST, Scope]]:
        """Every node of the module with the scope its names are looked up in."""
        if mod.path in self._scoped:
            return self._scoped[mod.path]
        out: list[tuple[ast.AST, Scope]] = []

        def visit(c, scope, cls_q):
            out.append((c, scope))
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # decorators, defaults and annotations run in the enclosing scope, the body in its own
                for d in c.decorator_list + [c.args] + ([c.returns] if c.returns else []):
                    visit(d, scope, None)
                fs = self.func_scope(mod, c, scope, ("sym", mod.path, cls_q) if cls_q else None)
                for st in c.body:
                    visit(st, fs, None)
            elif isinstance(c, ast.ClassDef):
                # the class body runs in the enclosing scope here; its methods see the module, not the class body
                q = (f"{cls_q}.{c.name}" if cls_q else c.name) if scope is mod.scope else None
                for d in c.decorator_list + c.bases + c.keywords:
                    visit(d, scope, None)
                for st in c.body:
                    visit(st, scope, q)
            else:
                for ch in ast.iter_child_nodes(c):
                    visit(ch, scope, cls_q if isinstance(c, (ast.If, ast.Try)) else None)

        for st in mod.tree.body:
            visit(st, mod.scope, None)
        self._scoped[mod.path] = out
        return out

    def def_node(self, ref: tuple | None) -> ast.AST | None:
        """The def or class a ("sym", path, qualname) names, else None."""
        if not ref or ref[0] != "sym":
            return None
        m = self.mod(ref[1])
        if m is None or m.tree is None:
            return None
        body, node = m.tree.body, None
        for part in ref[2].split("."):
            node = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                         and n.name == part), None)
            if node is None:
                return None
            body = node.body if isinstance(node, ast.ClassDef) else []
        return node

    # what a dotted module name and an attribute refer to

    def dotted(self, full: str, near: str | None) -> tuple:
        """A module path as imported: from the repo root first, then beside the importing file (a script run from
        its own folder imports its siblings by bare name)."""
        rel = full.replace(".", "/")
        for d in [""] + ([near] if near else []):
            p = f"{d}/{rel}" if d else rel
            if p + ".py" in self.paths:
                return ("module", p + ".py")
            if p in self.dirs:
                return ("pkg", p)
        return ("ext", full)

    def import_ref(self, mod: Mod, how: tuple, depth: int) -> tuple | None:
        _, module, name, level = how
        if level:
            base = Path(mod.dir) if mod.dir else Path()
            for _ in range(level - 1):
                base = base.parent
            full = ".".join([p for p in base.parts] + ([module] if module else []))
            m = self.dotted(full, None) if full else ("pkg", "")
        else:
            m = self.dotted(module, mod.dir)
        return m if name is None else self.attr(m, name, depth + 1)

    def mod_member(self, path: str, name: str, depth: int) -> tuple:
        m = self.mod(path)
        hows = m.scope.binds.get(name, []) if m is not None and m.scope is not None else []
        if depth < DEPTH and hows and all(h[0] == "import" for h in hows):
            r = self.import_ref(m, hows[0], depth + 1)  # a name the module imports: follow it to its definition
            if r is not None:
                return r
        return ("sym", path, name)

    def attr(self, ref: tuple | None, name: str, depth: int) -> tuple | None:
        if ref is None or depth > DEPTH:
            return None
        if ref[0] == "module":
            return self.mod_member(ref[1], name, depth)
        if ref[0] == "pkg":
            p = f"{ref[1]}/{name}" if ref[1] else name
            if p + ".py" in self.paths:
                return ("module", p + ".py")
            if p in self.dirs:
                return ("pkg", p)
            init = f"{ref[1]}/__init__.py" if ref[1] else "__init__.py"
            if init in self.paths:
                return self.mod_member(init, name, depth)
            return ("ext", p.replace("/", "."))
        if ref[0] == "sym":
            node = self.def_node(ref)
            if isinstance(node, ast.ClassDef):
                return self.member(ref, name, depth)
            if node is not None and not is_property(node):  # an attribute of a function object
                return ("sym", ref[1], f"{ref[2]}.{name}")
            # a property, a field or a variable: its attribute belongs to the class of the value it holds
            t = self.value_type(ref, depth + 1)
            return self.member(t, name, depth + 1) if t is not None else None
        return ("ext", f"{ref[1]}.{name}")

    def members(self, cls: tuple) -> dict[str, list[tuple]]:
        """What class `cls` defines itself: name -> ("def", node) | ("ann", annotation, scope) | ("value", expr,
        scope), from its body and from `self.x = ...` in its methods."""
        if cls not in self._members:
            out: dict[str, list[tuple]] = {}
            node, m = self.def_node(cls), self.mod(cls[1])
            if isinstance(node, ast.ClassDef) and m is not None:
                for st in node.body:
                    if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        out.setdefault(st.name, []).append(("def", st))
                    elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                        out.setdefault(st.target.id, []).append(("ann", st.annotation, m.scope))
                    elif isinstance(st, ast.Assign):
                        for t in st.targets:
                            if isinstance(t, ast.Name):
                                out.setdefault(t.id, []).append(("value", st.value, m.scope))
                for st in node.body:
                    if not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)) or not st.args.args:
                        continue
                    fs = self.func_scope(m, st, m.scope, cls)
                    me = st.args.args[0].arg
                    for n in _local_nodes(st.body):
                        tgt = ([(t, n.value) for t in n.targets] if isinstance(n, ast.Assign)
                               else [(n.target, None)] if isinstance(n, ast.AnnAssign) else [])
                        for t, v in tgt:
                            if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == me
                                    and not any(h[0] == "def" for h in out.get(t.attr, []))):
                                how = ("ann", n.annotation, fs) if isinstance(n, ast.AnnAssign) else ("value", v, fs)
                                out.setdefault(t.attr, []).append(how)
            self._members[cls] = out
        return self._members[cls]

    def _find(self, cls: tuple, name: str, depth: int) -> tuple | None:
        if depth > DEPTH:
            return None
        if name in self.members(cls):
            return ("sym", cls[1], f"{cls[2]}.{name}")
        node, m = self.def_node(cls), self.mod(cls[1])
        for b in getattr(node, "bases", []):
            br = self.ref_of(b, m.scope, depth + 1)
            if br and br[0] == "sym":
                f = self._find(br, name, depth + 1)
                if f:
                    return f
        return None

    def member(self, cls: tuple, name: str, depth: int) -> tuple | None:
        """An attribute of class `cls` (or of its instances): where it is defined, following base classes."""
        if cls[0] == "ext":
            return ("ext", f"{cls[1]}.{name}")
        if cls[0] != "sym":
            return None
        return self._find(cls, name, depth) or ("sym", cls[1], f"{cls[2]}.{name}")

    # references and types of expressions

    def lookup(self, scope: Scope, name: str) -> tuple[Scope | None, list[tuple] | None]:
        s = scope
        while s is not None:
            if name in s.binds:
                return s, s.binds[name]
            s = s.parent
        return None, None

    def ref_of(self, e: ast.AST, scope: Scope, depth: int = 0) -> tuple | None:
        """What a name or a dotted expression refers to (a module, class, function, method or field), or None."""
        if depth > DEPTH:
            return None
        if isinstance(e, ast.Name):
            s, hows = self.lookup(scope, e.id)
            if hows is None:
                return None if scope.mod.star else ("ext", f"builtins.{e.id}")
            refs = []
            for h in hows:
                if h[0] == "def":
                    refs.append(("sym", s.mod.path, h[1]))
                elif h[0] == "import":
                    refs.append(self.import_ref(s.mod, h, depth + 1))
                elif h[0] == "cls":
                    refs.append(h[1])
                else:
                    return None
            return refs[0] if refs and refs[0] is not None and all(r == refs[0] for r in refs) else None
        if isinstance(e, ast.Attribute):
            r = self.ref_of(e.value, scope, depth + 1)
            if r is not None:
                return self.attr(r, e.attr, depth + 1)
            t = self.type_of(e.value, scope, depth + 1)
            return self.member(t, e.attr, depth + 1) if t is not None else None
        return None

    def ann_type(self, a: ast.AST | None, scope: Scope, owner: tuple | None, depth: int) -> tuple | None:
        """The class an annotation names: `X`, `"X"`, `X | None`, `Optional[X]`, `Union[X, None]`, `Self`."""
        if a is None or depth > DEPTH:
            return None
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            try:
                return self.ann_type(ast.parse(a.value, mode="eval").body, scope, owner, depth + 1)
            except SyntaxError:
                return None
        opts = None
        if isinstance(a, ast.BinOp) and isinstance(a.op, ast.BitOr):
            opts = [a.left, a.right]
        elif isinstance(a, ast.Subscript):
            base = self.ref_of(a.value, scope, depth + 1)
            if base in (("ext", "typing.Optional"), ("ext", "typing.Union")):
                opts = list(a.slice.elts) if isinstance(a.slice, ast.Tuple) else [a.slice]
            else:
                return None
        if opts is not None:
            opts = [o for o in opts if not (isinstance(o, ast.Constant) and o.value is None)]
            return self.ann_type(opts[0], scope, owner, depth + 1) if len(opts) == 1 else None
        if isinstance(a, (ast.Name, ast.Attribute)):
            r = self.ref_of(a, scope, depth + 1)
            if r in (("ext", "typing.Self"), ("ext", "typing_extensions.Self")):
                return owner
            return r if r is not None and r[0] in ("sym", "ext") else None
        return None

    def _type_of_hows(self, hows: list[tuple], depth: int) -> tuple | None:
        """One type from a name's bindings; annotations win over assignments; disagreeing types give None."""
        anns = [h for h in hows if h[0] == "ann"]
        types = []
        for h in anns or hows:
            if h[0] == "ann":
                types.append(self.ann_type(h[1], h[2], h[2].owner, depth + 1))
            elif h[0] == "value":
                types.append(self.type_of(h[1], h[2], depth + 1))
            elif h[0] == "self":
                types.append(h[1])
            else:
                return None
        return types[0] if types and types[0] is not None and all(t == types[0] for t in types) else None

    def _memo(self, key: tuple, work):
        """A name's or a field's type, worked out once; a cycle (`x = x.next()`) gives None, not a loop."""
        got = self._types.get(key, PENDING_MISS)
        if got is PENDING:
            return None
        if got is not PENDING_MISS:
            return got
        self._types[key] = PENDING
        self._types[key] = work()
        return self._types[key]

    def value_type(self, ref: tuple, depth: int) -> tuple | None:
        """The type of the value a ("sym", ...) reference holds: a field, a property, a module-level variable."""
        if ref[0] != "sym" or depth > DEPTH:
            return None
        return self._memo(("value", ref), lambda: self._value_type(ref, depth))

    def _value_type(self, ref: tuple, depth: int) -> tuple | None:
        path, qual = ref[1], ref[2]
        if "." in qual:
            cls, name = qual.rsplit(".", 1)
            hows = self.members(("sym", path, cls)).get(name, [])
            m = self.mod(path)
            if len(hows) == 1 and hows[0][0] == "def":
                fn = hows[0][1]
                return self.ann_type(fn.returns, m.scope, ("sym", path, cls), depth + 1) if is_property(fn) else None
            return self._type_of_hows([(h[0], h[1], h[2]) for h in hows if h[0] != "def"], depth + 1) \
                if hows and all(h[0] != "def" for h in hows) else None
        m = self.mod(path)
        if m is None or m.scope is None:
            return None
        hows = m.scope.binds.get(qual, [])
        if hows and all(h[0] in ("ann", "value") for h in hows):
            return self._type_of_hows([(h[0], h[1], m.scope) for h in hows], depth + 1)
        return None

    def type_of(self, e: ast.AST, scope: Scope, depth: int = 0) -> tuple | None:
        """The class of the value of an expression, when the code says it (annotation, constructor, return
        annotation, field annotation); else None."""
        if depth > DEPTH:
            return None
        if isinstance(e, ast.Name):
            s, hows = self.lookup(scope, e.id)
            if not hows:
                return None
            return self._memo(("name", id(s), e.id), lambda: self._type_of_hows(
                [(h[0], h[1], s) if h[0] in ("ann", "value") else h for h in hows], depth + 1))
        if isinstance(e, ast.Call):
            f = self.ref_of(e.func, scope, depth + 1)
            node = self.def_node(f)
            if isinstance(node, ast.ClassDef):
                return f
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
                owner = ("sym", f[1], f[2].rsplit(".", 1)[0]) if "." in f[2] else None
                return self.ann_type(node.returns, self.mod(f[1]).scope, owner, depth + 1)
            return None
        if isinstance(e, ast.Attribute):
            r = self.ref_of(e, scope, depth + 1)
            return self.value_type(r, depth + 1) if r is not None else None
        return None

    # functions that take a value and use one of its attributes (a worker pool running `processor.process`)

    def enclosing(self, mod: Mod, line: int) -> str:
        """The qualname of the innermost def or class holding `line`, else "module level"."""
        if mod.path not in self._spans:
            spans: list[tuple[int, int, str]] = []

            def walk(node, prefix):
                for c in ast.iter_child_nodes(node):
                    if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        q = f"{prefix}.{c.name}" if prefix else c.name
                        spans.append((c.lineno, c.end_lineno, q))
                        walk(c, q)
                    else:
                        walk(c, prefix)

            if mod.tree is not None:
                walk(mod.tree, "")
            self._spans[mod.path] = spans
        inner = [s for s in self._spans[mod.path] if s[0] <= line <= s[1]]
        return min(inner, key=lambda s: s[1] - s[0])[2] if inner else "module level"

    def param_of(self, call: ast.Call, scope: Scope, at: int | str) -> tuple[tuple, str] | None:
        """The repo function `call` runs and the name of its parameter that receives argument `at` (a position in
        `call.args` or a keyword), or None when the callee is not a def in the repo or takes no such parameter.
        A method called on an instance (`obj.m(...)`, not `Class.m(...)`) gets the instance as its first one."""
        f = self.ref_of(call.func, scope)
        node = self.def_node(f)
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
        a = node.args
        pos = a.posonlyargs + a.args
        if isinstance(at, str):
            names = [x.arg for x in a.args + a.kwonlyargs]
            return (f, at) if at in names else None
        skip = 0
        owner = ("sym", f[1], f[2].rsplit(".", 1)[0]) if "." in f[2] else None
        if owner and isinstance(self.def_node(owner), ast.ClassDef) and not _decorated(node, "staticmethod"):
            on_class = isinstance(call.func, ast.Attribute) and isinstance(
                self.def_node(self.ref_of(call.func.value, scope)), ast.ClassDef)
            skip = 1 if (_decorated(node, "classmethod") or not on_class) else 0
        i = at + skip
        return (f, pos[i].arg) if i < len(pos) else None

    def uses_attr(self, fn: tuple, param: str, attr: str, depth: int = 0, seen: set | None = None) -> bool:
        """Whether repo function `fn` uses attribute `attr` of its parameter `param`: reads `param.attr` (calls it,
        or hands the bound method to a pool), or passes `param` on to a repo function that does."""
        seen = set() if seen is None else seen
        if depth > DEPTH or (fn, param, attr) in seen:
            return False
        seen.add((fn, param, attr))
        node, m = self.def_node(fn), self.mod(fn[1])
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or m is None:
            return False
        owner = ("sym", fn[1], fn[2].rsplit(".", 1)[0]) if "." in fn[2] else None
        owner = owner if owner and isinstance(self.def_node(owner), ast.ClassDef) else None
        sc = self.func_scope(m, node, m.scope, owner)
        if sc.binds.get(param, [None])[0] is None or len(sc.binds[param]) > 1:
            return False  # rebound in the body: the name no longer holds what the caller passed
        for n in _local_nodes(node.body):
            if (isinstance(n, ast.Attribute) and n.attr == attr and isinstance(n.value, ast.Name)
                    and n.value.id == param):
                return True
            if isinstance(n, ast.Call):
                given = [(i, x) for i, x in enumerate(n.args)] + [(k.arg, k.value) for k in n.keywords if k.arg]
                for at, x in given:
                    if isinstance(x, ast.Name) and x.id == param:
                        nxt = self.param_of(n, sc, at)
                        if nxt and self.uses_attr(nxt[0], nxt[1], attr, depth + 1, seen):
                            return True
        return False


def call_sites(code: Code, path: str, qual: str, skip: tuple[str, int, int] | None) -> tuple[list[dict], list[dict]]:
    """Where the head uses symbol `qual` of `path`: (resolved, possible).

    `git grep -l -w` finds the files that name it; `ast` then resolves each use in its scope. Resolved: a call
    whose callee resolves to this symbol (through the file's imports and aliases, a module-level def, `Class.m`,
    or a receiver whose class an annotation, a constructor, a return annotation or a field annotation gives), a
    construction for a class or its `__init__`, or a read (`x.p`) for a property. Possible: the same name on a
    receiver whose type the code does not say (or a file that does not parse). A same-name function elsewhere, or a
    receiver of another type, is never listed. `skip` = (path, from, to), the symbol's own body.

    Passed (`how` "passed", with `callee` and `caller`): the function or bound method given as an argument
    (`pool.imap(f, xs)`, `ex.submit(obj.m)`, `key=f`), or, for a method, an instance whose class has it given to a
    repo function that uses that attribute of its parameter, directly or through the functions it passes the
    parameter on to (`multiprocess_grid(cfg, Proc())` reaching `pool.imap(item_processor.process, ...)`). An
    instance given to a function outside the repo, or to one that never touches the method, is not a use.
    """
    target = ("sym", path, qual)
    node = code.def_node(target)
    prop = is_property(node)
    if qual.endswith(".__init__"):
        target = ("sym", path, qual.rsplit(".", 1)[0])
    name = target[2].split(".")[-1]
    cls = ("sym", path, target[2].rsplit(".", 1)[0]) if "." in target[2] else None
    member = cls is not None and isinstance(code.def_node(cls), ast.ClassDef)
    how = "read" if prop else "construct" if isinstance(code.def_node(target), ast.ClassDef) else "call"
    # a method's instances are passed under its class's name, which a file may name without the method's
    instances = member and how == "call" and not qual.endswith(".__init__")
    words = ["-e", name] + (["-e", cls[2].split(".")[-1]] if instances else [])
    r = subprocess.run(["git", "-C", code.repo, "grep", "-l", "-w", *words, code.head, "--", "*.py"],
                       capture_output=True, text=True, check=False)
    found, maybe = [], []
    for line in r.stdout.splitlines():
        p = line[len(code.head) + 1:] if line.startswith(code.head + ":") else line.split(":", 1)[-1]
        m = code.mod(p)
        if m is None:
            continue
        # line -> (resolved, how, extra); a resolved use on a line wins over a possible one
        hits: dict[int, tuple[bool, str, dict]] = {}
        if m.tree is None:  # unparsable at the head: the name followed by "(" on a line that is not a comment
            for i, t in enumerate(m.lines, 1):
                if re.search(rf"\b{re.escape(name)}\s*\(", t) and not t.strip().startswith("#"):
                    hits.setdefault(i, (False, how, {}))
        else:
            for n, scope in code.scoped(m):
                if not prop and isinstance(n, ast.Call):
                    for ln, ok, extra in _passed(code, m, n, scope, target, name, member, instances):
                        if ln not in hits or (ok and not hits[ln][0]):
                            hits[ln] = (ok, "passed", extra)
                if prop:
                    if not (isinstance(n, ast.Attribute) and n.attr == name and isinstance(n.ctx, ast.Load)):
                        continue
                    e, ln = n, n.end_lineno
                else:
                    if not isinstance(n, ast.Call):
                        continue
                    e = n.func
                    nm = e.id if isinstance(e, ast.Name) else e.attr if isinstance(e, ast.Attribute) else None
                    # a bare name may be an alias (`from m import f as g; g()`), so every bare call is resolved
                    if nm is None or (nm != name and not isinstance(e, ast.Name)):
                        continue
                    ln = e.end_lineno if isinstance(e, ast.Attribute) else n.lineno
                ref = code.ref_of(e, scope)
                if ref == target:
                    hits[ln] = (True, how, {})
                elif ref is None and ((isinstance(e, ast.Attribute) and member)
                                      or (isinstance(e, ast.Name) and e.id == name and m.star)):
                    hits.setdefault(ln, (False, how, {}))
        for ln in sorted(hits):
            if skip and p == skip[0] and skip[1] <= ln <= skip[2]:
                continue
            ok, hw, extra = hits[ln]
            site = {"path": p, "line": ln, "test": is_test(p), "text": m.lines[ln - 1].strip()[:110], "how": hw,
                    **extra}
            (found if ok else maybe).append(site)
    return found, maybe


def _passed(code: Code, m: Mod, call: ast.Call, scope: Scope, target: tuple, name: str, member: bool,
            instances: bool) -> list[tuple[int, bool, dict]]:
    """The arguments of `call` that hand `target` over (see `call_sites`): (line, resolved, callee and caller)."""
    out = []
    given = [(i, x) for i, x in enumerate(call.args)] + [(k.arg, k.value) for k in call.keywords if k.arg]
    for at, x in given:
        ok = None
        # every bare name is resolved (it may be an alias of the target); an attribute only when it is named so
        if isinstance(x, ast.Name) or (isinstance(x, ast.Attribute) and x.attr == name):
            ref = code.ref_of(x, scope)
            if ref == target:
                ok = True
            elif ref is None and isinstance(x, ast.Attribute) and member:
                ok = False  # `pool.map(obj.m)` on a receiver whose type the code does not say
        if ok is None and instances and not isinstance(x, ast.Lambda):
            t = code.type_of(x, scope)
            if t is not None and t[0] == "sym" and code.member(t, name, 0) == target:
                nxt = code.param_of(call, scope, at)
                if nxt and code.uses_attr(nxt[0], nxt[1], name):
                    ok = True
        if ok is not None:
            callee = ast.unparse(call.func)
            out.append((x.lineno, ok, {"callee": callee if len(callee) <= 40 else "..." + callee[-37:],
                                       "caller": code.enclosing(m, x.lineno)}))
    return out


def facts(repo: str, base: str, head: str) -> dict:
    """Everything the script knows about the MR from git and the code: files, symbols, call sites, the tree."""
    require_commit(repo, base)
    require_commit(repo, head)
    mb = git(repo, "merge-base", base, head).strip()
    counts = numstat(repo, mb, head)
    files = []
    for st, old_path, path in name_status(repo, mb, head):
        plus, minus = counts.get(path, (0, 0))
        status = {"A": "added", "D": "removed"}.get(st, "changed")
        f = {"path": path, "status": status, "plus": plus, "minus": minus, "symbols": []}
        if old_path != path:
            f["old_path"] = old_path
        if path.endswith(".py"):
            new_src = None if status == "removed" else show(repo, head, path)
            old_src = None if status == "added" else show(repo, mb, old_path)
            f["symbols"] = _symbols_of_file(new_src, old_src, hunks(repo, mb, head, old_path, path))
        files.append(f)
    changed_paths = {f["path"] for f in files}
    code = Code(repo, head)
    for f in files:
        for s in f["symbols"]:
            if s["status"] not in ("added", "changed"):
                continue
            s["property"] = is_property(code.def_node(("sym", f["path"], s["qualname"])))
            s["call_sites"], s["possible_sites"] = call_sites(code, f["path"], s["qualname"],
                                                              (f["path"], s["line"], s["line_to"]))
            for c in s["call_sites"] + s["possible_sites"]:
                c["in_mr"] = c["path"] in changed_paths
    tops = sorted(x for x in git(repo, "ls-tree", "-d", "--name-only", head).split() if not x.startswith("."))
    siblings = {}
    for d in sorted({str(Path(f["path"]).parent) for f in files}):
        r = subprocess.run(["git", "-C", repo, "ls-tree", "--name-only", head, (d + "/") if d != "." else ""],
                           capture_output=True, text=True, check=False)
        siblings[d] = [Path(x).name for x in r.stdout.split()]
    return {"repo": repo, "base": base, "merge_base": mb, "head": head, "files": files, "top_level": tops,
            "siblings": siblings}


# --- boxes, notes check, anchors ---


def box_id(path: str, qualname: str | None = None) -> str:
    return f"{path}::{qualname}" if qualname else path


def slug(bid: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", bid).strip("-").lower()


def boxes_of(fx: dict) -> list[dict]:
    """One box per file the MR touches and per added, changed, docstring-only or via-methods symbol in it (a test
    file is one box, its tests counted)."""
    out = []
    for f in fx["files"]:
        fb = {"id": box_id(f["path"]), "kind": "file", "path": f["path"], "name": Path(f["path"]).name,
              "status": f["status"], "line": 1, "plus": f["plus"], "minus": f["minus"]}
        if f.get("old_path"):
            fb["old_path"] = f["old_path"]
        out.append(fb)
        if is_test(f["path"]):
            tests = [s for s in f["symbols"] if s["qualname"].split(".")[-1].startswith("test_")]
            fb["tests_added"] = sum(1 for s in tests if s["status"] == "added")
            fb["tests_changed"] = sum(1 for s in tests if s["status"] == "changed")
            continue
        for s in f["symbols"]:
            if s["status"] in ("unchanged", "removed"):
                continue
            out.append({"id": box_id(f["path"], s["qualname"]), "kind": s["kind"], "path": f["path"],
                        "name": s["qualname"], "status": s["status"], "line": s["line"], "line_to": s["line_to"],
                        "parent": box_id(f["path"])})
    return out


def _edge(e: list) -> tuple[str, str, str, dict]:
    meta = e[3] if len(e) > 3 and isinstance(e[3], dict) else {}
    return e[0], e[1], e[2] if len(e) > 2 else "", meta


def check_notes(notes: dict, boxes: list[dict], fx: dict | None = None) -> tuple[list[str], list[str]]:
    """The checkable part of the notes against the facts.

    Problems: a note naming a box the MR does not have; a box with no description (docstring-only and
    via-methods boxes excepted); a changed symbol with no before vs after; an unknown pipeline stage; a data-flow
    node or edge that does not resolve; an edge whose `arg` is not a parameter (or field) of the box it enters, or
    whose `type` differs from that parameter's annotation; an edge whose `returns` differs from the return
    annotation of the box it leaves. Warnings: an edge into a function or class box that names no `arg`; a data-flow
    label that would clip even after wrapping (a node line wider than its box, an edge label off the drawing).
    """
    out: list[str] = []
    warn: list[str] = []
    ids = {b["id"]: b for b in boxes}
    df = notes.get("dataflow") or {"layers": [], "nodes": {}, "edges": []}
    named = [("boxes", k) for k in notes.get("boxes", {})]
    named += [("callers_pass", k) for k in notes.get("callers_pass", {})]
    named += [("before_after", x.get("box")) for x in notes.get("before_after", [])]
    named += [(f"dataflow node {k}", n["box"]) for k, n in df.get("nodes", {}).items() if n.get("box")]
    for where, k in named:
        if k not in ids:
            out.append(f"{where} names {k}, which is no box of the MR")
    for b in boxes:
        if b["id"] not in notes.get("boxes", {}) and b["status"] not in ("via-methods", "doc-only"):
            out.append(f"{b['id']}: {b['status']} but no description in notes.boxes")
    ba = {x.get("box") for x in notes.get("before_after", [])}
    for b in boxes:
        if b["status"] == "changed" and b["kind"] != "file" and b["id"] not in ba:
            out.append(f"{b['id']}: changed but no before vs after")
    stage = (notes.get("pipeline") or {}).get("stage")
    if stage is not None and stage not in STAGES:
        out.append(f"pipeline stage {stage!r} is not one of {STAGES} (or null)")
    if not notes.get("what"):
        out.append("notes.what (what this MR does) is empty")
    nodes = df.get("nodes", {})
    in_layers = [n for layer in df.get("layers", []) for n in layer]
    for n in in_layers:
        if n not in nodes:
            out.append(f"dataflow layer names node {n!r}, which has no entry in nodes")
    for n in nodes:
        if n not in in_layers:
            out.append(f"dataflow node {n!r} is in no layer")
    syms = {}
    for f in (fx or {}).get("files", []):
        for s in f["symbols"]:
            syms[box_id(f["path"], s["qualname"])] = s
    for e in df.get("edges", []):
        a, b, _, meta = _edge(e)
        for n in (a, b):
            if n not in nodes:
                out.append(f"dataflow edge {a} -> {b}: no node {n!r}")
        if a not in nodes or b not in nodes:
            continue
        tb = nodes[b].get("box")
        if tb in ids and ids[tb]["kind"] in SYMBOL_KINDS and tb in syms:
            params = {x["name"]: x["type"] for x in syms[tb]["fields"]}
            if "arg" not in meta:
                warn.append(f"dataflow edge {a} -> {b} into {tb} names no arg")
            elif meta["arg"] not in params:
                out.append(f"dataflow edge {a} -> {b}: {tb} has no argument {meta['arg']!r} "
                           f"(it takes {', '.join(params) or 'nothing'})")
            elif meta.get("type") and params[meta["arg"]] and meta["type"] != params[meta["arg"]]:
                out.append(f"dataflow edge {a} -> {b}: argument {meta['arg']} of {tb} is {params[meta['arg']]}, "
                           f"the edge says {meta['type']}")
        sa = nodes[a].get("box")
        if meta.get("returns") and sa in syms and syms[sa].get("returns") and meta["returns"] != syms[sa]["returns"]:
            out.append(f"dataflow edge {a} -> {b}: {sa} returns {syms[sa]['returns']}, the edge says {meta['returns']}")
    drawable = all(n in nodes for n in in_layers) and all(
        _edge(e)[0] in in_layers and _edge(e)[1] in in_layers for e in df.get("edges", []))
    if in_layers and drawable:
        warn += dataflow(df)[1]
    return out, warn


def anchor(repo: str, thread: dict, boxes: list[dict]) -> dict:
    """The box a GitLab thread is about (references/mr-overview.md, "Thread anchors").

    A line thread: the innermost def holding the line, read at the position's head (a new line) or base (a
    removed line), that is a box; else the file box, with the reason. A thread on the whole MR: the first
    backticked name in its notes equal to a symbol box's last name part. Neither: null (the MR itself).
    """
    ids = {b["id"]: b for b in boxes}
    renamed = {b["old_path"]: b["path"] for b in boxes if b["kind"] == "file" and b.get("old_path")}
    pos = thread.get("position") or {}
    if pos.get("new_line") or pos.get("old_line"):
        if pos.get("new_line"):
            path, line, sha = pos["new_path"], pos["new_line"], pos["head_sha"]
            now = path
        else:
            path, line, sha = pos.get("old_path") or pos["new_path"], pos["old_line"], pos["base_sha"]
            now = renamed.get(path, path)
        fbox = now if now in ids else None
        base = {"path": path, "line": line, "sha": sha}
        if not has_commit(repo, sha):
            return {"box": fbox, "how": f"line {line} at {sha[:9]}: that commit is not in the repo, file only", **base}
        syms = symbols_of(show(repo, sha, path))
        inner = sorted((q for q, s in syms.items() if s["from"] <= line <= s["to"]),
                       key=lambda q: syms[q]["to"] - syms[q]["from"])
        for q in inner:
            if box_id(now, q) in ids:
                return {"box": box_id(now, q), "how": f"line {line} at {sha[:9]} is inside {q}", **base}
        why = f"is inside {inner[0]}, which is no box" if inner else "is outside every function and class"
        return {"box": fbox, "how": f"line {line} at {sha[:9]} {why}: file only", **base}
    names = re.findall(r"`([A-Za-z_][\w.]*)`", " ".join(n.get("body", "") for n in thread.get("notes", [])))
    for nm in names:
        for b in boxes:
            if b["kind"] != "file" and b["name"].split(".")[-1] == nm.split(".")[-1]:
                return {"box": b["id"], "how": f"general thread names `{nm}`"}
    return {"box": None, "how": "general thread, no box named: the MR itself"}


# --- SVG ---

FILL = {"added": ("#e3f4ea", "#2f9e5f", "#0f5a33"), "changed": ("#fdefd3", "#c27a12", "#6e3a00"),
        "via-methods": ("#fff7e6", "#e0b060", "#6e3a00"), "doc-only": ("#f6f7f9", "#c27a12", "#46505c"),
        "removed": ("#fdebed", "#c0394b", "#7a1d2b"), "unchanged": ("#f1f3f6", "#c3cad3", "#46505c"),
        "data": ("#ffffff", "#8a94a2", "#16202b"), "existing": ("#eceff3", "#8a94a2", "#16202b"),
        "future": ("#ffffff", "#9aa3ad", "#5b6672"), "dir": ("#f6f7f9", "#c3cad3", "#16202b")}
HL = "#4338ca"
SANS = "system-ui,-apple-system,Segoe UI,sans-serif"
MONO = "ui-monospace,SF Mono,Menlo,Consolas,monospace"
W = 360  # drawing width in SVG units: a 390 px phone shows it at about 93 percent, no sideways scroll


def card_link(bid: str) -> str:
    return f"#card-{slug(bid)}"


def tw(s: str, size: float, mono: bool = False, bold: bool = False) -> float:
    """Estimated text width in SVG units: per character 0.61 of the size for monospace, 0.56 for sans, 0.6 for bold
    sans (DejaVu Sans Bold draws collection_resolution_area at 0.57)."""
    return len(s) * size * (0.61 if mono else 0.6 if bold else 0.56)


# Where a word too long for its line may break: after _ . / , - or between a lowercase letter and a capital.
BREAK = re.compile(r"(?<=[_./,-])(?=.)|(?<=[a-z0-9])(?=[A-Z])")


def wrap(s: str, width: float, size: float, mono: bool = False, bold: bool = False) -> list[str]:
    """Greedy lines of at most `width`; a word wider than `width` breaks at `BREAK` points. A piece with no break
    point that is still wider stays whole (`node_box` reports it as a clip)."""
    lines, cur = [], ""
    for word in s.split():
        pieces = [word] if tw(word, size, mono, bold) <= width else [p for p in BREAK.split(word) if p]
        for i, p in enumerate(pieces):
            t = cur + (" " if cur and i == 0 else "") + p
            if tw(t, size, mono, bold) > width and cur:
                lines.append(cur)
                cur = p
            else:
                cur = t
    return lines + ([cur] if cur else [])


def rect(x, y, w, h, kind, hl=False, dash=False, r=6) -> str:
    fill, stroke, _ = FILL[kind]
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r}" fill="{fill}" '
            f'stroke="{HL if hl else stroke}" stroke-width="{2.6 if hl else 1.2}"'
            + (' stroke-dasharray="4 3"' if dash else "") + "/>")


def text(x, y, s, size=11, weight=400, color="#16202b", mono=False, anchor="start") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{MONO if mono else SANS}" font-size="{size}" '
            f'font-weight="{weight}" fill="{color}" text-anchor="{anchor}">{esc(s)}</text>')


def svg_wrap(w, h, body, title) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h:.0f}" width="{w}" height="{h:.0f}" '
            f'role="img" aria-label="{esc(title)}" style="max-width:100%;height:auto">{body}</svg>')


def boxed(bid: str, body: str) -> str:
    return f'<a href="{esc(card_link(bid))}"><g data-box="{esc(bid)}">{body}</g></a>'


def repo_map_svg(fx: dict, boxes: list[dict], highlight: str | None = None, mini: bool = False,
                 root: str = "site3d") -> str:
    """The package tree: the MR's files (green added, amber changed, red deleted) with their classes and functions
    as chips; untouched packages summed in one line. `mini`: only the file of the highlighted box."""
    files = fx["files"]
    hb = next((b for b in boxes if b["id"] == highlight), None)
    if mini and hb:
        files = [f for f in files if f["path"] == hb["path"]]
    tops = sorted({f["path"].split("/")[0] for f in files})
    parts, y = [], 8
    if not mini:
        others = [t for t in fx["top_level"] if t not in tops]
        parts.append(text(8, y + 12, f"{root}/", 12, 700))
        parts.append(text(14 + tw(root + "/", 12) * 1.1, y + 12, f"{len(others)} other packages unchanged", 10.5, 400, "#5b6672"))
        y += 22
    shown_dirs: set[str] = set()
    for f in sorted(files, key=lambda f: f["path"]):
        segs = f["path"].split("/")
        for i in range(len(segs) - 1):
            d = "/".join(segs[: i + 1])
            if d in shown_dirs:
                continue
            shown_dirs.add(d)
            x = 8 + 14 * i
            parts.append(f'<path d="M{x - 6:.0f} {y:.0f} v10 h5" fill="none" stroke="#c3cad3"/>' if i else "")
            parts.append(text(x, y + 13, segs[i] + "/", 11.5, 600, "#3d4a58"))
            y += 19
        x = 8 + 14 * (len(segs) - 1)
        fb = next(b for b in boxes if b["id"] == f["path"])
        syms = [b for b in boxes if b.get("parent") == f["path"]]
        others_n = 0
        if mini and hb and hb["kind"] != "file":
            others_n = len([s for s in syms if s["id"] != hb["id"]])
            syms = [hb]
        cx0, cw_max = x + 8, W - x - 16
        rows, cur, cw = [], [], 0
        for s in syms:
            # a chip wider than its file box wraps its name at the BREAK points onto more lines
            lines = wrap(s["name"], cw_max - 14, 10.5, True)
            w = max(tw(ln, 10.5, True) for ln in lines) + 14
            if cur and cw + w > cw_max:
                rows.append(cur)
                cur, cw = [], 0
            cur.append((s, w, lines))
            cw += w + 5
        if cur:
            rows.append(cur)
        row_h = [18 + 13 * (max(len(c[2]) for c in row) - 1) + 4 for row in rows]
        unch = [s for s in f["symbols"] if s["status"] == "unchanged"]
        extra = []
        if unch and not mini:
            extra.append(f"{len(unch)} unchanged: " + ", ".join(s["qualname"] for s in unch[:3])
                         + (" ..." if len(unch) > 3 else ""))
        if others_n:
            extra.append(f"+ {others_n} more changed here")
        if f.get("old_path"):
            extra.append(f"moved from {f['old_path']}")
        sub = f"+{f['plus']} -{f['minus']}" if f["minus"] else f"+{f['plus']}"
        if f["status"] == "removed":
            sub = f"deleted -{f['minus']}"
        if "tests_added" in fb:
            tparts = [f"{fb['tests_added']} new tests" if fb["tests_added"] else "",
                      f"{fb['tests_changed']} changed" if fb["tests_changed"] else ""]
            sub = ", ".join(t for t in tparts if t) + (", " if any(tparts) else "") + sub
        sub = ("new  " if f["status"] == "added" else "") + sub
        # the file name on the left, its counts on the right of the same line when both fit with a 10-unit gap;
        # else the counts go on lines of their own under the name (a test file's "6 new tests, 12 changed, ...")
        inner = W - x - 4 - 16
        names = wrap(Path(f["path"]).name, inner, 12, bold=True)
        side = len(names) == 1 and tw(names[0], 12, bold=True) + 10 + tw(sub, 10.5, bold=True) <= inner
        subs = [] if side else wrap(sub, inner, 10.5, bold=True)
        head_h = 16 * len(names) + 14 * len(subs)
        h = 8 + head_h + sum(row_h) + 14 * len(extra) + (4 if rows else 0)
        g = [rect(x, y, W - x - 4, h, f["status"], hl=bool(hb and hb["id"] == fb["id"]))]
        for i, ln in enumerate(names):
            g.append(text(x + 8, y + 16 + 16 * i, ln, 12, 700, FILL[f["status"]][2]))
        if side:
            g.append(text(W - 10, y + 16, sub, 10.5, 600, FILL[f["status"]][2], anchor="end"))
        for i, ln in enumerate(subs):
            g.append(text(x + 8, y + 16 * len(names) + 14 + 14 * i, ln, 10.5, 600, FILL[f["status"]][2]))
        parts.append(boxed(fb["id"], "".join(g)))
        cy = y + 8 + head_h
        for row, rh in zip(rows, row_h):
            cx = cx0
            for s, w, lines in row:
                on = bool(hb and hb["id"] == s["id"])
                chip = rect(cx, cy, w, 18 + 13 * (len(lines) - 1), s["status"], hl=on, r=9) + "".join(
                    text(cx + 7, cy + 13 + 13 * i, ln, 10.5, 700 if on else 500, FILL[s["status"]][2], mono=True)
                    for i, ln in enumerate(lines))
                parts.append(boxed(s["id"], chip))
                cx += w + 5
            cy += rh
        for e in extra:
            ln = wrap(e, W - x - 24, 10)
            parts.append(text(cx0, cy + 10, ln[0] + (" ..." if len(ln) > 1 else ""), 10, 400, "#5b6672"))
            cy += 14
        y += h + 6
    if not mini:
        # siblings of the MR's folders, for orientation
        mr_paths = {f["path"] for f in files}
        for d in sorted({str(Path(f["path"]).parent) for f in files}):
            names = [s for s in fx["siblings"].get(d, []) if f"{d}/{s}" not in mr_paths]
            if not names or is_test(d + "/"):
                continue
            ln = wrap(f"unchanged in {d.split('/')[-1]}/: " + ", ".join(names), W - 90, 10)
            more = f" and {len(names) - ln[0].count(',') - 1} more" if len(ln) > 1 else ""
            parts.append(text(8, y + 11, ln[0].rstrip(",") + more, 10, 400, "#5b6672"))
            y += 15
    return svg_wrap(W, y + 4, "".join(parts), "Repository map of the MR")


def node_box(x, y, w, n, hl=False, clips: list[str] | None = None):
    """A data-flow box of width `w`; a line still wider than its box (no break point left) goes to `clips`."""
    lines = wrap(n["label"], w - 14, 11.5, bold=True)
    subs = wrap(n.get("sub", ""), w - 14, 10, True) if n.get("sub") else []
    if clips is not None:
        clips += [ln for ln in lines if tw(ln, 11.5, bold=True) > w - 14]
        clips += [ln for ln in subs if tw(ln, 10, True) > w - 14]
    h = 10 + 15 * len(lines) + 13 * len(subs) + 6
    kind = n["kind"] if n["kind"] in FILL else "data"
    g = [rect(x, y, w, h, kind, hl=hl, dash=n["kind"] == "future", r=8 if n["kind"] != "data" else 3)]
    ty = y + 17
    for ln in lines:
        g.append(text(x + w / 2, ty, ln, 11.5, 700, FILL[kind][2], anchor="middle"))
        ty += 15
    for ln in subs:
        g.append(text(x + w / 2, ty - 2, ln, 10, 400, "#3d4a58", mono=True, anchor="middle"))
        ty += 13
    return g, h


def dataflow_svg(df: dict) -> str:
    return dataflow(df)[0]


def dataflow(df: dict) -> tuple[str, list[str]]:
    """The data-flow drawing, and one line per label that would still clip (a node line wider than its box, an
    edge label running off the drawing)."""
    gap = 30
    pos, parts, y, clips = {}, [], 8, []
    tops, bottoms = [], []
    for li, layer in enumerate(df["layers"]):
        n = len(layer)
        w = 220 if n == 1 else (W - 30) / n
        hmax, built = 0, []
        for i, nid in enumerate(layer):
            x = (W - w) / 2 if n == 1 else 10 + i * (w + 10)
            cl: list[str] = []
            g, h = node_box(x, y, w, df["nodes"][nid], clips=cl)
            clips += [f"dataflow node {nid!r}: {ln!r} would clip in its {w:.0f}-unit box (put fewer boxes in its "
                      f"row, or a break point _ in the name)" for ln in cl]
            built.append((nid, x, g, h))
            hmax = max(hmax, h)
        for nid, x, g, h in built:
            nd = df["nodes"][nid]
            body = "".join(g)
            parts.append(boxed(nd["box"], body) if nd.get("box") else body)
            pos[nid] = (x, y, w, h, li)
        tops.append(y)
        bottoms.append(y + hmax)
        y += hmax + gap
    edges, labels = [], []
    for e in df["edges"]:
        a, b, label, _ = _edge(e)
        ax, ay, aw, ah, al = pos[a]
        bx, by, bw, bh, bl = pos[b]
        sx, sy = ax + aw / 2, ay + ah
        ex, ey = bx + bw / 2, by
        if bl - al > 1:  # skips a layer: around the side
            side = W - 3 if sx > W / 2 else 3
            sx2 = ax + aw if side > W / 2 else ax
            if (bx + bw / 2 > W / 2) == (side > W / 2):
                d = (f"M{sx2:.0f} {ay + ah / 2:.0f} H{side} V{by + bh / 2:.0f} "
                     f"H{(bx + bw + 2) if side > W / 2 else (bx - 2):.0f}")
            else:
                d = (f"M{sx2:.0f} {ay + ah / 2:.0f} H{side} V{ey - 10:.0f} "
                     f"H{ex + (12 if side > W / 2 else -12):.0f} V{ey - 2:.0f}")
            # the label sits beside the side line in the first gap below the source row, where no box covers it
            lx, ly = side - (5 if side > W / 2 else -5), (bottoms[al] + tops[al + 1]) / 2 + 3.5
            anc = "end" if side > W / 2 else "start"
        else:
            if abs(sx - ex) > 4:
                into = len([x for x in df["edges"] if x[1] == b])
                ex2 = ex + (12 if sx > ex else -12) if into > 1 else ex
                d = f"M{sx:.0f} {sy:.0f} C{sx:.0f} {sy + 16:.0f} {ex2:.0f} {ey - 16:.0f} {ex2:.0f} {ey - 2:.0f}"
                lx, ly = (sx + ex2) / 2 + 4, (sy + ey) / 2 + 3
            else:
                d = f"M{sx:.0f} {sy:.0f} V{ey - 2:.0f}"
                lx, ly = sx + 6, (sy + ey) / 2 + 4
            anc = "start"
        edges.append(f'<path d="{d}" fill="none" stroke="#5b6672" stroke-width="1.3" marker-end="url(#ah)"/>')
        if label:
            x0 = lx - {"start": 0, "end": 1}[anc] * tw(label, 9.5, True)
            if x0 < 0 or x0 + tw(label, 9.5, True) > W:
                clips.append(f"dataflow edge {a} -> {b}: label {label!r} would run off the drawing")
            labels.append(text(lx, ly, label, 9.5, 600, "#3d4a58", mono=True, anchor=anc).replace(
                "<text ", '<text stroke="#ffffff" stroke-width="3" paint-order="stroke" '))
    defs = ('<defs><marker id="ah" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" '
            'orient="auto-start-reverse"><path d="M0 0 L8 4 L0 8 z" fill="#5b6672"/></marker></defs>')
    # edge labels last: a label is never hidden under a box, wherever its edge runs
    return svg_wrap(W, max(y - gap + 10, 20), defs + "".join(edges) + "".join(parts) + "".join(labels),
                    "Data flow of the MR"), clips


def pipeline_svg(touched: set[str], stage: str | None) -> str:
    parts, y, ys = [], 6, {}
    for key, label, sub in PIPE:
        kind = "changed" if key in touched else "unchanged"
        parts.append(rect(10, y, 200, 36, kind, hl=key == stage))
        parts.append(text(20, y + 15, label, 11.5, 700, FILL[kind][2], mono=key != "raw"))
        parts.append(text(20, y + 29, sub, 10, 400, "#3d4a58"))
        ys[key] = y
        if key != PIPE[-1][0]:
            parts.append(f'<path d="M110 {y + 36} V{y + 50}" stroke="#5b6672" stroke-width="1.3" marker-end="url(#ah2)"/>')
        y += 52
    kind = "changed" if "sar_simulation" in touched else "unchanged"
    sy, iy = ys["sar_common"], ys["image_formation"]
    parts.append(rect(226, sy, 128, 36, kind, hl=stage == "sar_simulation"))
    parts.append(text(234, sy + 15, "sar_simulation", 11.5, 700, FILL[kind][2], mono=True))
    parts.append(text(234, sy + 29, "synthetic phase history", 10, 400, "#3d4a58"))
    parts.append(f'<path d="M290 {sy + 36} V{iy + 18} H214" fill="none" stroke="#5b6672" stroke-width="1.3" '
                 'marker-end="url(#ah2)"/>')
    defs = ('<defs><marker id="ah2" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" '
            'orient="auto-start-reverse"><path d="M0 0 L8 4 L0 8 z" fill="#5b6672"/></marker></defs>')
    return svg_wrap(W, y - 12, defs + "".join(parts), "Where the MR sits in the pipeline")


# --- HTML ---


def tag(who: str) -> str:
    """Which part the script made (exact) and which the worker wrote (checked against the facts)."""
    if who == "script":
        return '<span class="src s" title="read from the diff by the script">from the code</span>'
    return '<span class="src w" title="the reviewer\'s words, checked against the code">written</span>'


def where(caller: str) -> str:
    """Where a passed use sits: in a function, or at module level."""
    return "at module level" if caller == "module level" else f"in <code>{esc(caller)}</code>"


def fragment_parts(fx: dict, boxes: list[dict], notes: dict, web: str) -> dict:
    """The five levels as HTML and SVG pieces."""
    head = fx["head"]

    def code_link(path, line):
        return f"{web}/-/blob/{head}/{path}#L{line}"

    ba = {x["box"]: x for x in notes.get("before_after", [])}
    descr = notes.get("boxes", {})
    cards = []
    for f in fx["files"]:
        if not f["path"].endswith(".py") or is_test(f["path"]):
            continue
        for s in f["symbols"]:
            if s["status"] not in ("added", "changed"):
                continue
            bid = box_id(f["path"], s["qualname"])
            pill, word = {"added": ("ok", "new"), "changed": ("warn", "changed")}[s["status"]]
            old = {x["name"]: x for x in s["fields_before"]} if s.get("fields_before") is not None else None
            rows = []
            for fl in s["fields"]:
                meaning = re.sub(r"``?", "", s["doc_args"].get(fl["name"], ""))
                before = None
                if old is not None:
                    if fl["name"] not in old:
                        before = "new"
                    elif (old[fl["name"]]["type"], old[fl["name"]]["default"]) != (fl["type"], fl["default"]):
                        o = old[fl["name"]]
                        before = f"was {o['type']} = {o['default']}" if o["default"] else f"was {o['type']}"
                mark = f' <span class="pill {"ok" if before == "new" else "warn"} tiny">{esc(before)}</span>' if before else ""
                rows.append(f"<tr><td><code>{esc(fl['name'])}</code>{mark}</td><td><code>{esc(fl['type'])}</code>"
                            f"{(' = <code>' + esc(fl['default']) + '</code>') if fl['default'] else ''}</td>"
                            f"<td>{esc(meaning)}</td></tr>")
            sites = s.get("call_sites", [])
            prod = [c for c in sites if not c["test"]]
            tests = [c for c in sites if c["test"]]

            handed = any(c.get("how") == "passed" for c in sites)

            def link(c, handed=handed):
                a = (f'<a href="{esc(code_link(c["path"], c["line"]))}">'
                     f'{esc("/".join(c["path"].split("/")[-2:]))}:{c["line"]}</a>')
                if c.get("how") == "passed":
                    return f'passed to <code>{esc(c["callee"])}</code> {where(c["caller"])} ({a})'
                # beside a passed use, a call or read says its verb too
                return f'{"read" if c.get("how") == "read" else "called"} at {a}' if handed else a

            def links(cs):
                return ", ".join(link(c) for c in cs[:4]) + (f" and {len(cs) - 4} more" if len(cs) > 4 else "")

            maybe = s.get("possible_sites", [])
            reads = s.get("property")
            if prod:
                site_txt = links(prod) + (f"; {len(tests)} test lines" if tests else "")
            elif tests:
                site_txt = f"tests only, {len(tests)} lines"
            elif maybe:
                site_txt = f"no resolved {'reader' if reads else 'caller'}; possible ones below"
            else:
                site_txt = f"no {'reader' if reads else 'caller'} found in the repo"
            maybe_txt = ""
            if maybe:
                mp = [c for c in maybe if not c["test"]]
                mt = len(maybe) - len(mp)
                maybe_txt = (f'<p class="calls"><b>Possible (unresolved receiver)</b> {tag("script")} {links(mp)}'
                             + ("; " if mp and mt else "") + (f"{mt} test lines" if mt else "") + "</p>")
            # "Used" when a use hands it over (to a pool, `map`, a callback) rather than calling it
            used = "Callers" if not sites else "Used" if handed else "Read from" if reads else "Called from"
            change = ""
            if s["status"] == "changed" and bid in ba:
                change = (f'<p class="chg"><b>What changes</b> {tag("worker")} {esc(ba[bid]["after"])} '
                          f'<a href="#ba-{slug(bid)}">before vs after</a></p>')
            field_word = "field" if s["kind"] == "class" else "argument"
            cards.append(
                f'<div class="ocard" id="card-{slug(bid)}" data-box="{esc(bid)}"><p class="oh"><span class="pill {pill}">{word}</span> '
                f'<code class="qn">{esc(s["qualname"])}</code> <a class="loc" href="{esc(code_link(f["path"], s["line"]))}">'
                f'{esc(Path(f["path"]).name)}:{s["line"]}</a></p>'
                f'<p class="gen"><b>{"What it does" if s["status"] == "changed" else "What it is"}</b> {tag("worker")} '
                f'{esc(descr.get(bid, ""))}</p>{change}'
                f'<pre class="sig">{esc(s["signature"])}</pre>'
                + (f'<table class="ft"><tr><th>{field_word}</th><th>type</th><th>meaning (docstring) {tag("script")}</th></tr>'
                   f'{"".join(rows)}</table>' if rows else "")
                + f'<p class="calls"><b>{used}</b> {tag("script")} {site_txt}</p>' + maybe_txt
                + (f'<p class="calls"><b>Callers pass</b> {tag("worker")} {esc(notes["callers_pass"][bid])}</p>'
                   if bid in notes.get("callers_pass", {}) else "")
                + "</div>")
    syms = {box_id(f["path"], s["qualname"]): s for f in fx["files"] for s in f["symbols"]}
    # a before vs after on a file box counts the file's own diff lines
    syms.update({box_id(f["path"]): {"plus": f["plus"], "minus": f["minus"]} for f in fx["files"]})
    bas = []
    for x in notes.get("before_after", []):
        s = syms.get(x["box"], {})
        sig = ""
        if s.get("signature_before") and s.get("signature_before") != s.get("signature"):
            sig = f'<pre class="sig old">{esc(s["signature_before"])}</pre><pre class="sig new">{esc(s["signature"])}</pre>'
        bas.append(f'<div class="ba" id="ba-{slug(x["box"])}"><p class="oh"><code class="qn">{esc(x["box"].split("::")[-1])}</code> '
                   f'<span class="muted">+{s.get("plus", 0)} -{s.get("minus", 0)} lines {tag("script")}</span></p>'
                   f'<p class="gen"><b>What it does</b> {tag("worker")} {esc(descr.get(x["box"], ""))}</p>'
                   f'<div class="cols"><div class="b"><b>Before</b><p>{esc(x["before"])}</p></div>'
                   f'<div class="a"><b>After</b><p>{esc(x["after"])}</p></div></div>{sig}</div>')
    calls_line = []
    for f in fx["files"]:
        if is_test(f["path"]):
            continue
        idle = []
        for s in f["symbols"]:
            if s["status"] in ("added", "changed") and not s["qualname"].split(".")[-1].startswith("_"):
                # resolved uses only: a possible one (unresolved receiver) stays on the symbol's card
                outside = [c for c in s.get("call_sites", []) if not c["test"] and c["path"] != f["path"]]
                if outside:
                    calls_line.append(
                        f'<li><code>{esc(s["qualname"])}</code>: '
                        + ", ".join({"passed": f'passed to <code>{esc(c.get("callee", ""))}</code> at ',
                                     "read": "read from "}.get(c.get("how"), "called from ")
                                    + f'<a href="{esc(code_link(c["path"], c["line"]))}">{esc(c["path"])}:{c["line"]}</a>'
                                    for c in outside[:3])
                        + (f" and {len(outside) - 3} more" if len(outside) > 3 else "") + "</li>")
                else:
                    idle.append(s["qualname"])
        if idle:
            calls_line.append(f'<li>Used only inside <code>{esc(Path(f["path"]).name)}</code> and the tests: '
                              + ", ".join(f"<code>{esc(q)}</code>" for q in idle) + "</li>")
    terms = "".join(f"<li><b>{esc(k)}</b>: {esc(v)}</li>" for k, v in notes.get("terms", {}).items())
    doc_only = [s["qualname"] for f in fx["files"] for s in f["symbols"] if s["status"] == "doc-only"]
    others = "".join(
        f'<li><code>{esc(f["path"])}</code> {esc(descr.get(f["path"], ""))}</li>'
        for f in fx["files"] if is_test(f["path"]) or not f["path"].endswith(".py") or f["status"] == "removed")
    touched = {f["path"].split("/")[0] for f in fx["files"]}
    pipe = notes.get("pipeline") or {}
    df = notes.get("dataflow") or {"layers": [], "nodes": {}, "edges": []}
    return {
        "what": f'<p class="what">{tag("worker")} {esc(notes.get("what", ""))}</p>'
                + (f'<details class="terms"><summary>Terms used here</summary><ul>{terms}</ul></details>' if terms else ""),
        "others": (f'<p class="calls"><b>Tests, docs and deleted files</b> {tag("worker")}</p><ul class="callers">{others}</ul>'
                   if others else ""),
        "doc_only": ('<p class="calls"><b>Docstring only</b> ' + tag("script") + " "
                     + ", ".join(f"<code>{esc(q)}</code>" for q in doc_only)
                     + ": only docstrings differ; the code itself is unchanged.</p>") if doc_only else "",
        "map": repo_map_svg(fx, boxes, root=web.rstrip("/").split("/")[-1]),
        "flow": dataflow_svg(df),
        "pipe": pipeline_svg(touched, pipe.get("stage")),
        "pipe_note": f'<p class="note">{tag("worker")} {esc(pipe.get("note", ""))}</p><ul class="callers">{"".join(calls_line[:6])}</ul>',
        "cards": "".join(cards),
        "ba": "".join(bas),
    }


# The overview is a light panel whatever page holds it: every rule is scoped under .mro and sets its own colors,
# so the dark review briefing and the light app show the same thing.
CSS = """
.mro{background:#fff;color:#16202b;border:1px solid #dde2e8;border-radius:12px;padding:12px 14px;margin:0 0 14px;
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;text-align:left}
.mro *{box-sizing:border-box}.mro h2{font-size:1.05rem;margin:0 0 6px;color:#16202b}
.mro a{color:#4338ca}.mro p,.mro li,.mro td,.mro th,.mro summary,.mro b{color:#16202b}
.mro p,.mro li{overflow-wrap:anywhere}
.mro code{font-family:ui-monospace,"SF Mono","IBM Plex Mono",Menlo,Consolas,monospace;font-size:.88em;background:#eef1f5;
color:#16202b;padding:0 4px;border-radius:4px;overflow-wrap:anywhere}
.mro .pill{display:inline-flex;font-size:.78rem;font-weight:600;line-height:1.2;padding:3px 8px;border-radius:999px;white-space:nowrap}
.mro .pill.tiny{font-size:.7rem;padding:1px 6px;white-space:normal}.mro .pill.ok{color:#0f6b3c;background:#e3f4ea}
.mro .pill.warn{color:#8a4500;background:#fdefd3}.mro .pill.neut{color:#46505c;background:#eceff3}
.mro details.lvl{border-top:1px solid #dde2e8;padding:8px 0}.mro details.lvl>summary{font-weight:700;cursor:pointer}
.mro details.lvl>summary .n{color:#5b6672;font-weight:600;margin-right:6px}
.mro .src{font-size:.68rem;font-weight:700;padding:1px 5px;border-radius:4px;vertical-align:1px;white-space:nowrap}
.mro .src.s{background:#e9e8fd;color:#3b33a8}.mro .src.w{background:#fdf0e7;color:#a3360c}
.mro .what{margin:4px 0 8px}.mro details.terms{font-size:.9rem;margin:0 0 6px}.mro details.terms ul{margin:4px 0;padding-left:20px}
.mro .note{margin:6px 0}.mro .callers{margin:4px 0;padding-left:20px;font-size:.92rem}.mro .callers a,.mro .calls a{overflow-wrap:anywhere}
.mro .legend{font-size:.8rem;color:#5b6672;margin:4px 0}
.mro .sw{display:inline-block;width:12px;height:10px;border-radius:2px;margin:0 3px 0 8px;vertical-align:0}
.mro .sw.added{background:#e3f4ea;border:1px solid #2f9e5f}.mro .sw.changed{background:#fdefd3;border:1px solid #c27a12}
.mro .sw.via{background:#fff7e6;border:1px solid #e0b060}.mro .sw.doc{background:#f6f7f9;border:1px solid #c27a12}
.mro .sw.removed{background:#fdebed;border:1px solid #c0394b}
.mro .ocard,.mro .ba{border:1px solid #dde2e8;border-radius:10px;padding:8px 10px;margin:8px 0;background:#fcfcfd}
.mro .oh{margin:0 0 4px}.mro .qn{font-weight:700;background:none;padding:0}.mro .loc{font-size:.85rem;margin-left:4px}
.mro .gen,.mro .chg,.mro .calls{margin:4px 0;font-size:.92rem}.mro .muted{color:#5b6672;font-size:.85rem}
.mro pre.sig{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:.78rem;background:#f6f8fa;color:#16202b;
border:0;border-radius:6px;padding:6px 8px;margin:6px 0;white-space:pre-wrap;overflow-wrap:anywhere}
.mro pre.sig.old{background:#fdebed}.mro pre.sig.new{background:#e6f5ec}
.mro table.ft{border-collapse:collapse;font-size:.82rem;width:100%;margin:4px 0;background:none;table-layout:fixed}
.mro table.ft th:nth-child(1){width:30%}.mro table.ft th:nth-child(2){width:28%}
.mro table.ft td,.mro table.ft th{overflow-wrap:anywhere;word-break:break-word}
.mro table.ft th,.mro table.ft td{border:0;border-top:1px solid #dde2e8;padding:3px 4px;text-align:left;vertical-align:top;background:none}
.mro table.ft td code{overflow-wrap:anywhere}
.mro .cols{display:grid;grid-template-columns:1fr;gap:6px}.mro .cols>div{border-radius:8px;padding:6px 8px;font-size:.9rem}
.mro .cols p{margin:2px 0}.mro .cols .b{background:#fdebed}.mro .cols .a{background:#e6f5ec}
.mro .ov-grid{display:grid;grid-template-columns:1fr;gap:10px}.mro .ov-grid>div{min-width:0}
.mro svg{max-width:100%;height:auto;background:none}
@media (min-width:900px){.mro .ov-grid{grid-template-columns:380px 1fr}.mro .cols{grid-template-columns:1fr 1fr}
.mro .cardsgrid{display:grid;grid-template-columns:1fr 1fr;gap:0 12px}.mro .cardsgrid .ocard{margin:6px 0}}
"""

LEGEND = ('<p class="legend"><span class="sw added"></span>added <span class="sw changed"></span>changed '
          '<span class="sw via"></span>changed in a method <span class="sw doc"></span>docstring only '
          '<span class="sw removed"></span>deleted · a box opens its card</p>')


def tags_legend() -> str:
    """One line where the tags first appear (a cold reader of !789 asked what they mean)."""
    return (f'<p class="legend tags">{tag("worker")} the reviewer\'s words; {tag("script")} read from the diff.</p>')


def fragment(fx: dict, parts: dict) -> str:
    """The overview section, styles inside, between the embed markers."""
    return (f'{START}<section class="mro" id="mr-overview"><style>{CSS}</style>'
            f'<h2>What this MR does <span class="pill neut">at {fx["head"][:9]}</span></h2>{tags_legend()}{parts["what"]}'
            f'<details class="lvl" open><summary><span class="n">1</span>Repo map</summary>{LEGEND}'
            f'<div class="ov-grid"><div>{parts["map"]}</div>'
            f'<div><details class="lvl" open><summary><span class="n">2</span>Data flow</summary>{parts["flow"]}</details>'
            f'<details class="lvl" open><summary><span class="n">3</span>Where it plugs in</summary>{parts["pipe"]}'
            f'{parts["pipe_note"]}</details></div></div></details>'
            f'<details class="lvl" open><summary><span class="n">4</span>Classes and functions</summary>'
            f'<div class="cardsgrid">{parts["cards"]}</div>{parts["others"]}</details>'
            f'<details class="lvl" open><summary><span class="n">5</span>Before vs after</summary>'
            f'{parts["ba"] or "<p class=muted>No existing behavior changes.</p>"}{parts["doc_only"]}</details>'
            f'</section>{END}')


def page(fx: dict, parts: dict, title: str) -> str:
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><style>body{{margin:0;padding:12px;background:#f6f7f9}}</style></head>'
            f'<body>{fragment(fx, parts)}</body></html>')


def cold_text(parts: dict) -> str:
    """The overview's words, for the cold reader (the drawings go as a PNG)."""
    h = "\n".join(parts[k] for k in ("what", "pipe_note", "cards", "others", "ba", "doc_only"))
    h = re.sub(r'<span class="src [sw]"[^>]*>[^<]*</span>', "", h)
    h = re.sub(r"</(p|li|div|tr|pre|summary)>", "\n", h)
    t = html.unescape(re.sub(r"<[^>]+>", " ", h))
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip() + "\n"


# --- safety: no script, no handler, no outside link in a drawing ---


def page_problems(h: str) -> list[str]:
    out = []
    low = h.lower()
    if "<script" in low:
        out.append("has a script")
    if re.search(r"<[^>]*\son[a-z]+\s*=", low):
        out.append("has an event handler attribute (on...=)")
    if "javascript:" in low:
        out.append("has a javascript: link")
    for t in ("iframe", "object", "embed"):
        if f"<{t}" in low:
            out.append(f"has an <{t}>")
    return out


def svg_problems(svg: str) -> list[str]:
    out = page_problems(svg)
    if "foreignobject" in svg.lower():
        out.append("has a foreignObject")
    if re.search(r"<image\b", svg, re.IGNORECASE):
        out.append("has an image")
    for m in re.finditer(r"""\b(?:xlink:)?href\s*=\s*["']([^"']*)["']""", svg):
        if not m.group(1).startswith("#card-"):
            out.append(f"links outside the overview: {m.group(1)[:60]}")
    if re.search(r"url\(\s*(?!#)", svg):
        out.append("references an outside resource with url(...)")
    return out


# --- build and check ---


def names(head: str) -> dict:
    h = head[:12]
    return {"page": f"overview-{h}.html", "data": f"overview-{h}.json", "text": f"overview-{h}.txt",
            "map": f"overview-{h}-map.svg", "flow": f"overview-{h}-flow.svg", "pipe": f"overview-{h}-pipe.svg"}


def mini_name(head: str, disc: str) -> str:
    return f"overview-{head[:12]}-mini-{disc[:8]}.svg"


def build(out: Path, fx: dict, notes: dict, threads: list[dict] | None, repo: str | None = None,
          project: str = "product/site3d") -> dict:
    """Write the overview files for `fx` and `notes` into `out`; return the record written as the JSON."""
    out.mkdir(parents=True, exist_ok=True)
    repo = repo or fx["repo"]
    boxes = boxes_of(fx)
    probs, warn = check_notes(notes, boxes, fx)
    anchors = {t["id"]: anchor(repo, t, boxes) for t in threads or []}
    parts = fragment_parts(fx, boxes, notes, f"https://{HOST}/{project}")
    n = names(fx["head"])
    (out / n["page"]).write_text(page(fx, parts, f"What this MR does, at {fx['head'][:9]}"))
    (out / n["map"]).write_text(parts["map"])
    (out / n["flow"]).write_text(parts["flow"])
    (out / n["pipe"]).write_text(parts["pipe"])
    (out / n["text"]).write_text(cold_text(parts))
    for disc, a in anchors.items():
        if a["box"]:
            a["mini"] = mini_name(fx["head"], disc)
            (out / a["mini"]).write_text(repo_map_svg(fx, boxes, highlight=a["box"], mini=True,
                                                      root=project.split("/")[-1]))
    rec = {"schema": 1, "project": project, "head_sha": fx["head"], "base_sha": fx["merge_base"],
           "made_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "facts": fx, "notes": notes, "boxes": boxes, "anchors": anchors,
           "check": {"problems": probs, "warnings": warn}}
    (out / n["data"]).write_text(json.dumps(rec, indent=1) + "\n")
    return rec


def check_dir(d: Path, head: str) -> tuple[list[str], list[str]]:
    """Every way the overview in `d` at `head` breaks the rules (module docstring, `check`); and warnings."""
    n = names(head)
    p = d / n["data"]
    if not p.exists():
        return [f"{n['data']}: missing (no overview at {head[:9]})"], []
    rec = json.loads(p.read_text())
    if rec.get("head_sha") != head:
        return [f"{n['data']}: written at {str(rec.get('head_sha'))[:9]}, not {head[:9]}"], []
    boxes = boxes_of(rec["facts"])
    out, warn = check_notes(rec["notes"], boxes, rec["facts"])
    if [b["id"] for b in boxes] != [b["id"] for b in rec.get("boxes", [])]:
        out.append(f"{n['data']}: its boxes differ from the boxes of its facts")
    if not (d / n["page"]).exists():
        out.append(f"{n['page']}: missing")
    else:
        out += [f"{n['page']}: {x}" for x in page_problems((d / n["page"]).read_text())]
    svgs = [n["map"], n["flow"], n["pipe"]]
    ids = {b["id"] for b in boxes}
    for disc, a in rec.get("anchors", {}).items():
        if a.get("box") is not None and a["box"] not in ids:
            out.append(f"thread {disc[:8]} anchored to {a['box']}, which is no box")
        if a.get("box"):
            svgs.append(mini_name(head, disc))
    for s in svgs:
        if not (d / s).exists():
            out.append(f"{s}: missing")
        else:
            out += [f"{s}: {x}" for x in svg_problems((d / s).read_text())]
    return out, warn


def report(probs: list[str], warn: list[str], ok: str) -> int:
    for p in probs:
        print(p)
    for w in warn:
        print(f"warn: {w}")
    if probs:
        return 1
    print(ok + (f", {len(warn)} warnings" if warn else ""))
    return 0


def embed(d: Path, head: str, into: Path) -> None:
    """Put the overview fragment under the first <h1> of `into` (or replace the one already there)."""
    src = (d / names(head)["page"]).read_text()
    frag = src[src.index(START): src.index(END) + len(END)]
    txt = into.read_text()
    if START in txt and END in txt:
        txt = txt[: txt.index(START)] + frag + txt[txt.index(END) + len(END):]
    elif "</h1>" in txt:
        i = txt.index("</h1>") + len("</h1>")
        txt = txt[:i] + frag + txt[i:]
    else:
        m = re.search(r"<body[^>]*>", txt)
        i = m.end() if m else 0
        txt = txt[:i] + frag + txt[i:]
    tmp = into.with_name(f".{into.name}.tmp")
    tmp.write_text(txt)
    tmp.replace(into)


def _load_json(p: str | None):
    return json.loads(Path(p).read_text()) if p else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("facts", help="files, symbols, call sites from git and the code")
    p.add_argument("repo")
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--out", required=True, help="FACTS.json")
    p = sub.add_parser("build", help="drawings, page, anchors, record")
    p.add_argument("dir")
    p.add_argument("--facts", required=True)
    p.add_argument("--notes", required=True)
    p.add_argument("--threads", default=None, help="mr_discussions.py --all --json output")
    p.add_argument("--repo", default=None, help="the checkout the facts came from (default: the one they name)")
    p.add_argument("--project", default="product/site3d")
    p = sub.add_parser("check", help="the note checks and the drawing safety checks; exit 1 on any problem")
    p.add_argument("dir")
    p.add_argument("--head", required=True)
    p = sub.add_parser("make", help="facts, build and check in one call")
    p.add_argument("repo")
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--notes", required=True)
    p.add_argument("--threads", default=None)
    p.add_argument("--project", default="product/site3d")
    p.add_argument("--out", required=True, help="output folder (the MR's mr_context folder, or a scratch one)")
    p = sub.add_parser("embed", help="put the overview under the first <h1> of a page")
    p.add_argument("dir")
    p.add_argument("--head", required=True)
    p.add_argument("--into", required=True)
    a = ap.parse_args()
    if a.cmd == "facts":
        fx = facts(a.repo, a.base, a.head)
        Path(a.out).write_text(json.dumps(fx, indent=1) + "\n")
        print(f"{a.out}: {len(fx['files'])} files, {len(boxes_of(fx))} boxes")
        return 0
    if a.cmd == "build":
        fx = _load_json(a.facts)
        rec = build(Path(a.dir), fx, _load_json(a.notes), _load_json(a.threads), a.repo, a.project)
        print(f"{a.dir}: {len(rec['boxes'])} boxes, {len(rec['anchors'])} threads")
        return 0
    if a.cmd == "check":
        probs, warn = check_dir(Path(a.dir), a.head)
        return report(probs, warn, f"ok: overview at {a.head[:9]}")
    if a.cmd == "make":
        fx = facts(a.repo, a.base, a.head)
        rec = build(Path(a.out), fx, _load_json(a.notes), _load_json(a.threads), a.repo, a.project)
        print(f"{len(fx['files'])} files, {len(rec['boxes'])} boxes")
        for disc, x in rec["anchors"].items():
            print(f"thread {disc[:8]}: {x['box']} ({x['how']})")
        probs, warn = check_dir(Path(a.out), a.head)
        return report(probs, warn, f"ok: overview at {a.head[:9]} in {a.out}")
    if a.cmd == "embed":
        embed(Path(a.dir), a.head, Path(a.into))
        print(f"{a.into}: overview at {a.head[:9]} under the title")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
