#!/usr/bin/env python3
"""Check an MR description against the code at the MR head: names that are gone, test counts that are off.

Usage (read only; GitLab is only read, never written):
    description_drift.py --mr IID [--project product/site3d] [--repo PATH] [--out DIR] [--json] [--strict]
    description_drift.py --description FILE --head REF [--base REF | --target main] [--repo PATH] [...]
    description_drift.py --description FILE --facts FACTS.json.gz        (offline, on saved head facts)

Reported, each in its own section (a list for a person to check, then fix the description):
    code names    backticked spans, snake_case, CamelCase, dotted module paths, file paths and CLI flags that are
                  not at the head: not a tracked path or folder, not an identifier or word in any file there
    acronyms      2+ capitals (OMP, IAA, L4) found neither as a word nor as an identifier part in the files the
                  MR changes; a removed method, or plain prose (NVIDIA)
    rare words    4+ letter prose words found nowhere in the repository at the head (a plural, -ed, -ing or -ly
                  ending is stripped first); a removed method (debiasing), or plain English (eleven)
    test counts   "N tests", "N new tests", "N unit tests" against the test functions the MR adds (at the head,
                  not at the base) and the existing ones whose code it changes; a claim passes when it equals
                  the added total, added plus changed, or one file's added count. Counted with `ast`:
                  module-level `test*` functions and `test*` methods of `Test*` classes; parametrize cases are
                  not expanded. "3 GPU-only tests" and other qualified counts are listed, not judged.
A "Merge order" section (the team's list of the other MRs in a stack) is not checked.

The base is `git merge-base origin/<target> <head>` unless --base is given (target from GitLab with --mr, else
--target, default main). The head commit must exist in --repo; fetch it first. Exit 0 (it is a report);
--strict exits 1 when anything is listed; 2 on an input error.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from glab_common import PROJECT, api

WORD_RE = r"[A-Za-z_][A-Za-z0-9_]*"
FLAG_RE = r"--[A-Za-z][A-Za-z0-9_-]*"
URL_RE = re.compile(r"(?:https?://|www\.)\S+")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
SNAKE_RE = re.compile(r"(?<![\w./-])[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?![\w/-])")
CAMEL_RE = re.compile(r"(?<![\w.])(?:[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+|[a-z]+[A-Z][A-Za-z0-9]*)(?![\w])")
BARE_FLAG_RE = re.compile(r"(?<![\w-])--[a-z][a-z0-9_-]+")
BARE_PATH_RE = re.compile(r"(?<![\w/.])[\w.-]+(?:/[\w.-]+)+\.[A-Za-z]{1,5}(?![\w/])")
ACRONYM_RE = re.compile(r"(?<![\w-])(?:[A-Z][A-Z0-9]*[A-Z][A-Z0-9]*|[A-Z]+[0-9]+[A-Z0-9]*)(?![\w-])")
PROSE_WORD_RE = re.compile(r"(?<![\w-])[A-Za-z]{4,}(?![\w])")
COUNT_RE = re.compile(r"\b(\d+)\s+(?:(new|unit|added)\s+)?tests?\b")
SUBSET_RE = re.compile(r"\b(\d+)\s+((?:[A-Za-z][\w-]*\s+){1,2}?)tests?\b")
CAMEL_PART_RE = re.compile(r"[A-Z]+[0-9]*(?![a-z])|[A-Z]?[a-z0-9]+|[0-9]+")
SUFFIXES = ("s", "es", "ed", "d", "ing", "ly")
SKIP_HEADING_RE = re.compile(r"#+\s*merge order\b", re.IGNORECASE)  # lists other MRs, not this one's code


# ----------------------------------------------------------------------------------------------------- git facts
def _git(repo: str, *args: str, check: bool = True) -> str:
    out = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=False)
    if check and out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def _parts(token: str) -> set[str]:
    """Lowercased identifier parts: split on underscores and camel humps, plus each part without trailing digits."""
    out: set[str] = set()
    for piece in re.split(r"_+", token):
        if not piece:
            continue
        out.add(piece.lower())
        for p in CAMEL_PART_RE.findall(piece):
            out.add(p.lower())
            if p.rstrip("0123456789"):
                out.add(p.rstrip("0123456789").lower())
    return out


def _vocab(repo: str, head: str, paths: list[str] | None = None) -> tuple[set[str], set[str]]:
    """(identifiers, parts) of every text file at head, or of `paths` only; both lowercased."""
    args = ["grep", "-h", "-I", "-o", "-E", "-e", WORD_RE, head, "--"]
    if paths is not None:
        if not paths:
            return set(), set()
        args += paths
    tokens = set(_git(repo, *args, check=False).split())
    idents = {t.lower() for t in tokens}
    parts: set[str] = set()
    for t in tokens:
        parts |= _parts(t)
    return idents, parts - idents


def _test_functions(src: str) -> dict[str, str]:
    """Test name to its source: module-level test* functions and test* methods of Test* classes."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}
    out: dict[str, str] = {}
    funcs = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        if isinstance(node, funcs) and node.name.startswith("test"):
            out[node.name] = ast.get_source_segment(src, node) or ""
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for m in node.body:
                if isinstance(m, funcs) and m.name.startswith("test"):
                    out[f"{node.name}::{m.name}"] = ast.get_source_segment(src, m) or ""
    return out


def _is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def collect_facts(repo: str, base: str, head: str) -> dict:
    """Everything the check needs from git at base and head (read only)."""
    head_sha = _git(repo, "rev-parse", f"{head}^{{commit}}").strip()
    base_sha = _git(repo, "rev-parse", f"{base}^{{commit}}").strip()
    paths = _git(repo, "ls-tree", "-r", "--name-only", head_sha).splitlines()
    status = _git(repo, "diff", "--name-status", "-M", base_sha, head_sha).splitlines()
    changed: dict[str, str | None] = {}  # head path -> base path (None when new)
    for line in status:
        f = line.split("\t")
        if f[0].startswith("D"):
            continue
        if f[0][0] in "RC":
            changed[f[2]] = f[1]
        else:
            changed[f[1]] = None if f[0].startswith("A") else f[1]
    idents, parts = _vocab(repo, head_sha)
    mr_idents, mr_parts = _vocab(repo, head_sha, sorted(changed))
    flags = set(_git(repo, "grep", "-h", "-I", "-o", "-E", "-e", FLAG_RE, head_sha, "--", check=False).split())
    added: dict[str, list[str]] = {}
    touched: dict[str, list[str]] = {}
    for path, old in sorted(changed.items()):
        if not _is_test_file(path):
            continue
        at_head = _test_functions(_git(repo, "show", f"{head_sha}:{path}"))
        at_base = _test_functions(_git(repo, "show", f"{base_sha}:{old}", check=False)) if old else {}
        new = sorted(n for n in at_head if n not in at_base)
        changed_tests = sorted(n for n in at_head if n in at_base and at_head[n] != at_base[n])
        if new:
            added[path] = new
        if changed_tests:
            touched[path] = changed_tests
    return {
        "head": head_sha, "base": base_sha, "paths": paths, "mr_paths": sorted(changed),
        "idents": sorted(idents), "parts": sorted(parts),
        "mr_words": sorted(mr_idents | mr_parts), "flags": sorted(flags),
        "tests": {"added": added, "touched": touched},
    }


def save_facts(facts: dict, path: str | Path) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(facts, f, separators=(",", ":"))


def load_facts(path: str | Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------------------------------------ extraction
def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    """The text with the given spans replaced by spaces (positions kept)."""
    chars = list(text)
    for s, e in spans:
        chars[s:e] = " " * (e - s)
    return "".join(chars)


def _other_mr_sections(text: str) -> list[tuple[int, int]]:
    """Spans of sections about other MRs (a "Merge order" heading up to the next heading)."""
    spans, start = [], None
    for m in re.finditer(r"^.*$", text, flags=re.MULTILINE):
        if re.match(r"#+\s", m.group(0)):
            if start is not None:
                spans.append((start, m.start()))
                start = None
            if SKIP_HEADING_RE.match(m.group(0)):
                start = m.start()
    if start is not None:
        spans.append((start, len(text)))
    return spans


def extract(text: str) -> dict:
    """Code-like names, acronyms, prose words and test-count claims, each with its line."""
    urls = [m.span() for m in URL_RE.finditer(text)]
    text_nourl = _blank(text, urls + _other_mr_sections(text))
    ticks = list(BACKTICK_RE.finditer(text_nourl))
    prose = _blank(text_nourl, [m.span() for m in ticks])
    names: list[dict] = []
    for m in ticks:
        span = m.group(1).strip()
        if " " not in span:
            span = re.sub(r"\(.*\)$", "", span)  # foo_bar() -> foo_bar
        names.append({"kind": "backtick", "name": span, "line": _line_of(text, m.start())})
    for kind, rx in (("flag", BARE_FLAG_RE), ("path", BARE_PATH_RE), ("snake", SNAKE_RE), ("camel", CAMEL_RE)):
        for m in rx.finditer(prose):
            names.append({"kind": kind, "name": m.group(0).rstrip("."), "line": _line_of(text, m.start())})
        if kind in ("flag", "path"):
            prose_for_words = _blank(prose, [m.span() for m in rx.finditer(prose)])
            prose = prose_for_words
    acronyms = [{"name": m.group(0), "line": _line_of(text, m.start())} for m in ACRONYM_RE.finditer(prose)]
    words = [{"name": m.group(0), "line": _line_of(text, m.start())} for m in PROSE_WORD_RE.finditer(prose)]
    counts, subsets = [], []
    for m in COUNT_RE.finditer(text_nourl):
        counts.append({"claimed": int(m.group(1)), "text": m.group(0), "line": _line_of(text, m.start())})
    claimed_at = {m.start() for m in COUNT_RE.finditer(text_nourl)}
    for m in SUBSET_RE.finditer(text_nourl):
        if m.start() not in claimed_at:
            subsets.append({"text": m.group(0), "line": _line_of(text, m.start())})
    return {"names": names, "acronyms": acronyms, "words": words, "test_counts": counts, "subset_counts": subsets}


# -------------------------------------------------------------------------------------------------- judgment
class _Head:
    def __init__(self, facts: dict):
        self.paths = set(facts["paths"])
        self.dirs = {p.rsplit("/", i)[0] for p in self.paths for i in range(1, p.count("/") + 1)}
        self.top = {p.split("/", 1)[0] for p in self.paths}
        self.basenames = {p.rsplit("/", 1)[-1] for p in self.paths}
        self.modules = {b.rsplit(".", 1)[0].lower() for b in self.basenames} | {d.rsplit("/", 1)[-1].lower()
                                                                               for d in self.dirs}
        self.idents = set(facts["idents"])
        self.words = self.idents | set(facts["parts"])
        self.mr_words = set(facts["mr_words"])
        self.flags = set(facts["flags"])

    def ident(self, name: str) -> bool:
        return name.lower() in self.idents or name.lower() in self.modules

    def path(self, p: str) -> bool | None:
        """True or False for a repo path; None for something path-shaped that is not one (a branch name)."""
        p = re.sub(r":\d+(-\d+)?$", "", p.strip().lstrip("./")).rstrip("/")
        if p in self.paths or p in self.dirs:
            return True
        last = p.rsplit("/", 1)[-1]
        if "." not in last and p.split("/", 1)[0] not in self.top:
            return None
        return False

    def dotted(self, name: str) -> bool:
        parts = name.split(".")
        if re.fullmatch(r"[A-Za-z]{1,5}", parts[-1]) and name in self.basenames | self.paths:
            return True  # a file name such as formation.py
        if re.fullmatch(r"(py|md|toml|yaml|yml|json|cu|txt|sh|cfg|csv|npz)", parts[-1]):
            return any(p.endswith("/" + name) or p == name for p in self.paths)
        for i in range(len(parts), 0, -1):
            mod = "/".join(parts[:i])
            if f"{mod}.py" in self.paths or mod in self.dirs:
                return all(self.ident(p) for p in parts[i:])
        return self.ident(parts[-1])

    def stem_known(self, word: str) -> bool:
        w = word.lower()
        if w in self.words:
            return True
        for suf in SUFFIXES:
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                root = w[: -len(suf)]
                if root in self.words or (suf == "ing" and root + "e" in self.words):
                    return True
        return False


def _code_tokens(span: str) -> list[str]:
    """The code-like tokens of one backtick span (a call's arguments and operators dropped)."""
    span = span.strip()
    if URL_RE.match(span):
        return []
    if " " not in span:
        span = re.sub(r"\(.*$", "", span)  # foo(x) -> foo
        return [span] if re.search(r"[A-Za-z]", span) else []
    toks = re.findall(r"--?[A-Za-z][\w-]*|[\w./-]+", span)
    return [t for t in toks if re.search(r"[_./]|[a-z][A-Z]|^--", t) and re.search(r"[A-Za-z]", t)]


def _name_missing(head: _Head, tok: str) -> bool | None:
    """True when a code-like token is not at the head; None when it is not checkable."""
    tok = tok.strip().strip(",;:")
    if not tok:
        return None
    if tok.startswith("--"):
        return tok not in head.flags
    if tok.startswith(("-", "!", "#")):
        return None
    if "/" in tok:
        found = head.path(tok)
        return None if found is None else not found
    if "." in tok:
        return not head.dotted(tok)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tok):
        return None
    return not head.ident(tok)


def _check_counts(claims: list[dict], tests: dict) -> list[dict]:
    added = {f: len(v) for f, v in tests["added"].items()}
    touched = {f: len(v) for f, v in tests["touched"].items()}
    n_add, n_touch = sum(added.values()), sum(touched.values())
    accepted = {n_add, n_add + n_touch, *added.values()}
    return [{**c, "ok": c["claimed"] in accepted, "added": n_add, "changed": n_touch,
             "added_per_file": added, "changed_per_file": touched} for c in claims]


def check(description: str, facts: dict) -> dict:
    """Every name, acronym, word and test count in the description that the head does not back."""
    head = _Head(facts)
    ex = extract(description)
    code, seen = [], set()
    for n in ex["names"]:
        toks = _code_tokens(n["name"]) if n["kind"] == "backtick" else [n["name"]]
        for tok in toks:
            tok = re.sub(r"\(.*$", "", tok).strip(",;:")
            if (n["kind"] == "backtick" and " " not in n["name"] and re.fullmatch(r"[A-Za-z][a-z]+", tok)
                    and head.stem_known(tok)):
                continue  # a plain word in backticks, e.g. `chief`, used as text in the code
            if tok in seen or not _name_missing(head, tok):
                continue
            seen.add(tok)
            code.append({"name": tok, "line": n["line"], "kind": n["kind"]})
    acr, seen_a = [], set()
    for a in ex["acronyms"]:
        name = a["name"]
        if name in seen_a or name.lower() in head.mr_words or name.lower().rstrip("s") in head.mr_words:
            continue
        seen_a.add(name)
        acr.append(a)
    rare, seen_w = [], set()
    for w in ex["words"]:
        key = w["name"].lower()
        if key in seen_w or head.stem_known(key):
            continue
        seen_w.add(key)
        rare.append({"name": w["name"], "line": w["line"]})
    return {
        "head": facts["head"], "base": facts["base"], "mr_files": len(facts["mr_paths"]),
        "code_names": code, "acronyms": acr, "rare_words": rare,
        "test_counts": _check_counts(ex["test_counts"], facts["tests"]),
        "subset_counts": ex["subset_counts"],
    }


def findings_count(report: dict) -> int:
    return (len(report["code_names"]) + len(report["acronyms"]) + len(report["rare_words"])
            + sum(not c["ok"] for c in report["test_counts"]))


def render(report: dict, label: str) -> str:
    """Plain-text report, one line per item."""
    title = (f"Description drift: {label} head {report['head'][:9]} base {report['base'][:9]} "
             f"({report['mr_files']} files changed)")
    out = [title, ""]
    sections = (
        ("Code names not at the head", report["code_names"], lambda i: f"`{i['name']}` (line {i['line']})"),
        ("Acronyms not in the MR's own files (a removed method, or plain prose)", report["acronyms"],
         lambda i: f"{i['name']} (line {i['line']})"),
        ("Words found nowhere in the repository (a removed method, or plain English)", report["rare_words"],
         lambda i: f"{i['name']} (line {i['line']})"),
    )
    for title, items, fmt in sections:
        out.append(f"{title}: {len(items)}")
        out += [f"  {fmt(i)}" for i in items]
        out.append("")
    out.append("Test counts:")
    if not report["test_counts"]:
        out.append("  (the description states no test count)")
    for c in report["test_counts"]:
        per = ", ".join(f"{f.rsplit('/', 1)[-1]} {n}" for f, n in c["added_per_file"].items()) or "none"
        verdict = "matches" if c["ok"] else "WRONG"
        out.append(f"  \"{c['text']}\" (line {c['line']}): {verdict}; the MR adds {c['added']} test functions "
                   f"({per}) and changes {c['changed']} existing ones")
    for s in report["subset_counts"]:
        out.append(f"  not judged (a subset): \"{s['text']}\" (line {s['line']})")
    out.append("")
    out.append(f"{findings_count(report)} item(s) to check. Counts are test functions (parametrize cases not "
               "expanded).")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------------------------------------------- CLI
def _base_for(repo: str, target: str, head: str) -> str:
    for ref in (f"origin/{target}", target):
        if _git(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False).strip():
            return _git(repo, "merge-base", ref, head).strip()
    raise SystemExit(f"no origin/{target} or {target} in {repo}; pass --base")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--mr", help="MR iid: description, head sha and target branch read from GitLab (GET only)")
    src.add_argument("--description", help="a description file (with --head, or --facts)")
    ap.add_argument("--head", help="head ref for --description")
    ap.add_argument("--base", help="base ref (default: merge-base of origin/<target> and the head)")
    ap.add_argument("--target", default="main", help="target branch for the default base with --description")
    ap.add_argument("--project", default=PROJECT, help="GitLab project path (default %(default)s)")
    ap.add_argument("--repo", default=".", help="git checkout holding the head commit (default: cwd)")
    ap.add_argument("--facts", help="read saved head facts instead of git (offline)")
    ap.add_argument("--save-facts", help="write the head facts (gzip JSON) for an offline rerun or a test fixture")
    ap.add_argument("--out", help="folder for report.txt, report.json and facts.json.gz")
    ap.add_argument("--json", action="store_true", help="print the JSON report instead of text")
    ap.add_argument("--strict", action="store_true", help="exit 1 when anything is listed")
    a = ap.parse_args(argv)

    if a.mr:
        mr = api(f"projects/{quote(a.project, safe='')}/merge_requests/{a.mr}")
        if not isinstance(mr, dict) or "sha" not in mr:
            print(f"no MR {a.mr} in {a.project}: {mr}", file=sys.stderr)
            return 2
        description, head, target = mr.get("description") or "", mr["sha"], mr.get("target_branch") or "main"
        label = f"!{a.mr}"
    else:
        description = Path(a.description).read_text()
        head, target, label = a.head, a.target, a.description
        if not head and not a.facts:
            print("--description needs --head (or --facts)", file=sys.stderr)
            return 2
    if a.facts:
        facts = load_facts(a.facts)
    else:
        if subprocess.run(["git", "-C", a.repo, "cat-file", "-e", f"{head}^{{commit}}"],
                          capture_output=True, check=False).returncode != 0:
            print(f"head {head} is not in {a.repo}; run `git -C {a.repo} fetch origin` first", file=sys.stderr)
            return 2
        base = a.base or _base_for(a.repo, target, head)
        facts = collect_facts(a.repo, base, head)
    report = check(description, facts)
    report["label"] = label
    text = render(report, label)
    if a.save_facts:
        save_facts(facts, a.save_facts)
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.txt").write_text(text)
        (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
        (out / "description.md").write_text(description)
        save_facts(facts, out / "facts.json.gz")
    print(json.dumps(report, indent=1) if a.json else text, end="" if not a.json else "\n")
    return 1 if a.strict and findings_count(report) else 0


if __name__ == "__main__":
    sys.exit(main())
