#!/usr/bin/env python3
"""Trace an MR line to where its choice was made: fact log, blame, port commit, dev line, transcripts.

Blame on a split MR stops at the port commit the split wrote (author time 12:00:00, a body that
describes the module, not the line). This follows the line past it:

1. "Jisu said" fact log lines that name the file, the function or the line's names (read first).
2. Blame at the MR revision; a port commit is one listed in a port map, or one whose author time of
   day is exactly 12:00:00 (only the split writes it).
3. The function: --symbol, else the enclosing def at the revision (ast), else a name the comment
   quotes in backticks that the file defines (--comment, or "body" in a batch line), else on a blank
   line the def that ends just above it.
4. The dev line (default origin/main..jisu/sar_apps): the commit that introduced `def <name>`, the
   newest commits that touched the function (`git log -L`), and the first commit that added each of
   the line's distinctive names or its text (`git log -S`); a distinctive name (dotted, snake or
   camel case) is also searched across the whole dev line, since a demo can hard-code a choice before
   the file exists. A line with none of these (a blank line, a module docstring) gets the commit that
   first added the file. Port commits are left out.
5. Transcripts, read only: `rg -l -F` over ~/.claude*/projects for the function name and the line's
   most distinctive name, and for each found commit's subject (the session, and so the task, that
   wrote it: subjects survive restacks, SHAs do not).
6. Which MR of the stack holds the blamed commit (--mr IID=BASE..HEAD, repeat).

Usage:
    line_origin.py --repo R --rev SHA --file PATH --line N [--symbol NAME] [--json]
    line_origin.py --batch LINES.json --out DIR      LINES: [{"label", "repo", "rev", "file", "line",
                                                      "symbol"?, "body"?, "mr"?}]; writes results.json and
                                                      one NN-<label>.txt per line
    common: [--dev RANGE] [--port-map GLOB|none ...] [--facts FILE] [--transcripts DIR ...]
            [--mr IID=BASE..HEAD ...]

Port maps: /home/ubuntu/src/site3d_project_root/outputs/port_maps/<YYYY-MM-DD>-<split>.json, written
by the split task (format in references/thread-explanation.md). Standard library plus git and rg.
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import keyword
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_DEV = "origin/main..jisu/sar_apps"
DEFAULT_PORT_MAPS = "/home/ubuntu/src/site3d_project_root/outputs/port_maps/*.json"
DEFAULT_FACTS = "/home/ubuntu/.config/astra/areas/1a1c25b6/facts.md"
DEFAULT_TRANSCRIPTS = "~/.claude*/projects"
FMT = "%H%x1f%aI%x1f%an%x1f%s"
COMMON = {"self", "None", "True", "False", "return", "import", "from", "print", "range", "float", "int", "str",
          "list", "dict", "tuple", "np", "cp", "len", "assert", "pytest"}


def git(repo: str, *args: str) -> tuple[int, str]:
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.returncode, r.stdout


def _commits(out: str) -> list[dict]:
    rows = []
    for ln in out.splitlines():
        parts = ln.split("\x1f")
        if len(parts) == 4 and re.fullmatch(r"[0-9a-f]{40}", parts[0]):
            rows.append({"sha": parts[0], "date": parts[1], "author": parts[2], "subject": parts[3]})
    return rows


def _is_port_time(iso: str) -> bool:
    return iso[11:19] == "12:00:00"


def blame(repo: str, rev: str, path: str, line: int) -> dict:
    rc, out = git(repo, "blame", "--line-porcelain", "-L", f"{line},{line}", rev, "--", path)
    if rc != 0 or not out:
        return {"sha": None}
    info: dict = {"sha": out.split()[0]}
    for ln in out.splitlines()[1:]:
        if ln.startswith("\t"):
            info["text"] = ln[1:]
        else:
            k, _, v = ln.partition(" ")
            info[k] = v
    _, iso = git(repo, "log", "-1", "--format=%aI", info["sha"])
    return {"sha": info["sha"], "date": iso.strip(), "author": info.get("author"), "subject": info.get("summary"),
            "text": info.get("text", "")}


def enclosing_symbol(source: str, line: int) -> str | None:
    """The innermost function or class whose body (decorators included) holds the line."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            if start <= line <= (node.end_lineno or node.lineno):
                if best is None or start >= best[0]:
                    best = (start, node.name)
    return best[1] if best else None


def defined_names(source: str) -> dict[str, tuple[int, int]]:
    """Every function and class in a file: name -> (first line, last line)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    return {n.name: (n.lineno, n.end_lineno or n.lineno) for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def pick_symbol(source: str, line: int, text: str, names: list[str]) -> tuple[str | None, str]:
    """The function a line belongs to, and how it was picked.

    The enclosing def; else a name the comment quotes in backticks that the file defines; else, on a
    blank line, the def that ends at most two lines above it (a comment anchored just after a function).
    """
    sym = enclosing_symbol(source, line)
    if sym:
        return sym, "enclosing"
    defs = defined_names(source)
    for n in names:
        if n.split(".")[-1] in defs:
            return n.split(".")[-1], "named in the comment"
    if not text.strip():
        above = [(end, name) for name, (_, end) in defs.items() if line - 2 <= end < line]
        if above:
            return max(above)[1], "def just above a blank line"
    return None, "module level"


def comment_names(body: str) -> list[str]:
    """Names a comment quotes in backticks (call parentheses dropped)."""
    out = []
    for m in re.finditer(r"`+([^`]+?)`+", body or ""):
        name = re.split(r"[\s(\[]", m.group(1).strip(), maxsplit=1)[0]
        if re.fullmatch(r"[A-Za-z_][\w.]*", name) and name not in out:
            out.append(name)
    return out


def line_tokens(text: str) -> list[str]:
    """Search terms for the dev line: the stripped line, then dotted names, literals and long names."""
    code = re.sub(r"\s+#.*$", "", text).strip()
    out: list[str] = []
    if len(code) >= 12:
        out.append(code)
    out += re.findall(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b", code)
    out += [s for s in re.findall(r"""(["'])(.{4,60}?)\1""", code) for s in [s[1]]]
    for name in re.findall(r"\b[A-Za-z_]\w*\b", code):
        if len(name) >= 4 and not keyword.iskeyword(name) and name not in COMMON:
            out.append(name)
    seen: list[str] = []
    for t in out:
        if t not in seen:
            seen.append(t)
    return seen[:6]


def distinctive(tok: str) -> bool:
    """A name specific enough to search the whole dev line for: LookDirection.RIGHT, ray_batch, RaytracingConfig."""
    if " " in tok or tok.startswith(("np.", "xp.", "cp.", "math.", "self.", "os.")):
        return False
    if "." in tok:
        return True
    return len(tok) >= 8 and ("_" in tok.strip("_") or bool(re.search(r"[a-z][A-Z]", tok)))


def _read_maps(globs: list[str]) -> list[dict]:
    maps = []
    for g in globs:
        if g == "none":
            continue
        for f in sorted(glob.glob(os.path.expanduser(g))):
            try:
                maps.append(json.loads(Path(f).read_text()) | {"_file": f})
            except (OSError, json.JSONDecodeError):
                continue
    return maps


def port_info(commit: dict, maps: list[dict]) -> dict:
    for m in maps:
        for c in m.get("commits", []):
            if commit.get("sha") and str(c.get("port", "")).startswith(commit["sha"][:9]):
                return {"is_port": True, "why": f"port map {Path(m['_file']).name}", "map": c}
    if commit.get("date") and _is_port_time(commit["date"]):
        return {"is_port": True, "why": "author time 12:00:00", "map": None}
    return {"is_port": False, "why": "", "map": None}


def file_added(repo: str, dev: str, path: str) -> dict | None:
    """The dev-line commit that first added a file with this path, else with this file name."""
    for spec in (path, f"*/{Path(path).name}"):
        _, log = git(repo, "log", "--reverse", "--diff-filter=A", f"--format={FMT}", dev, "--", spec)
        rows = [c for c in _commits(log) if not _is_port_time(c["date"])]
        if rows:
            return rows[0]
    return None


def dev_search(repo: str, dev: str, symbol: str | None, tokens: list[str], paths: list[str],
               file: str | None = None) -> dict:
    tip = dev.split("..")[-1]
    out: dict = {"range": dev, "paths": paths, "introduced": None, "function_commits": [], "token_commits": [],
                 "file_added": None}
    if symbol and not paths:
        rc, hit = git(repo, "grep", "-l", "-E", rf"^\s*(async\s+)?(def|class)\s+{re.escape(symbol)}\b", tip, "--")
        out["paths"] = paths = [ln.split(":", 1)[1] for ln in hit.splitlines() if ":" in ln][:3]
    if symbol:
        for pat in (f"def {symbol}(", f"class {symbol}"):
            _, log = git(repo, "log", "--reverse", f"--format={FMT}", "-S", pat, dev)
            rows = [c for c in _commits(log) if not _is_port_time(c["date"])]
            if rows:
                out["introduced"] = rows[0]
                break
        for p in paths:
            _, log = git(repo, "log", f"--format={FMT}", "-s", "-L", f":{symbol}:{p}", dev)
            out["function_commits"] += [c for c in _commits(log) if not _is_port_time(c["date"])][:5]
    for tok in tokens:
        _, log = git(repo, "log", "--reverse", f"--format={FMT}", "-S", tok, dev, "--", *paths)
        rows = [c for c in _commits(log) if not _is_port_time(c["date"])]
        row = {"token": tok, "first": rows[0] if rows else None, "hits": len(rows), "first_anywhere": None}
        if paths and distinctive(tok):
            # The choice can predate the file: a demo or script elsewhere on the dev line hard-coded it first.
            _, log = git(repo, "log", "--reverse", f"--format={FMT}", "-S", tok, dev)
            anywhere = [c for c in _commits(log) if not _is_port_time(c["date"])]
            if anywhere and (not rows or anywhere[0]["date"] < rows[0]["date"]):
                row["first_anywhere"] = anywhere[0]
        out["token_commits"].append(row)
    if file and not out["introduced"] and not any(t["first"] for t in out["token_commits"]):
        # A blank or module-level line (a docstring): the choice is the file's, made where it was first written.
        out["file_added"] = file_added(repo, dev, (paths or [file])[0])
    return out


def transcript_dirs(spec: list[str]) -> list[str]:
    dirs = []
    for s in spec:
        for d in glob.glob(os.path.expanduser(s)):
            real = os.path.realpath(d)
            if os.path.isdir(real) and real not in dirs:
                dirs.append(real)
    return dirs


def _project(path: str, dirs: list[str]) -> tuple[str, str | None]:
    for d in dirs:
        if path.startswith(d + os.sep):
            proj = Path(path).relative_to(d).parts[0]
            m = re.search(r"-runs-(T-.+)$", proj)
            return proj, (m.group(1) if m else None)
    return Path(path).parent.name, None


def search_transcripts(term: str, dirs: list[str], keep: int = 5) -> dict:
    """Sessions whose transcript holds the term: count, and the oldest few with a snippet."""
    if not dirs or not term:
        return {"count": 0, "files": []}
    r = subprocess.run(["rg", "-l", "-F", "--glob", "*.jsonl", "--", term, *dirs], capture_output=True, text=True)
    files = sorted(set(r.stdout.split()), key=lambda f: os.path.getmtime(f) if os.path.exists(f) else 0)
    rows = []
    for f in files[:keep]:
        s = subprocess.run(["rg", "-o", "-m", "1", "-e", f".{{0,160}}{re.escape(term)}.{{0,160}}", f],
                           capture_output=True, text=True).stdout
        snippet = s.splitlines()[0] if s else ""
        snippet = snippet.replace("\\n", " ").replace('\\"', '"')
        proj, task = _project(f, dirs)
        rows.append({"file": f, "project": proj, "task": task,
                     "last_write": time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(f))),
                     "snippet": snippet[:330]})
    return {"count": len(files), "files": rows}


def read_facts(path: str | None, terms: list[str]) -> list[str]:
    if not path or not os.path.exists(path):
        return []
    norm = [t.lower().replace("_", " ") for t in terms if len(t) >= 4]
    hits = []
    for ln in Path(path).read_text().splitlines():
        if not ln.startswith("- "):
            continue
        low = ln.lower().replace("_", " ")
        if any(t in low for t in norm):
            hits.append(ln)
    return hits


def which_mr(repo: str, sha: str | None, mrs: dict[int, str]) -> int | None:
    if not sha:
        return None
    for iid, rng in mrs.items():
        base, _, head = rng.partition("..")
        in_head = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", sha, head]).returncode == 0
        in_base = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", sha, base]).returncode == 0
        if in_head and not in_base:
            return iid
    return None


def subject_term(subject: str) -> str:
    """A commit subject cut before any quote or backslash, so it matches inside a JSON transcript."""
    return re.split(r"[\"\\]", subject or "", maxsplit=1)[0][:60].strip()


def trace(repo: str, rev: str, path: str, line: int, dev: str = DEFAULT_DEV, transcripts: list[str] | None = None,
          facts: str | None = DEFAULT_FACTS, port_maps: list[str] | None = None, mrs: dict[int, str] | None = None,
          symbol: str | None = None, comment: str = "") -> dict:
    """Everything known about where one line's choice was made (see the module docstring).

    `comment` is the reviewer's note: names it quotes in backticks pick the function when the line
    itself does not (a blank line, a module docstring).
    """
    t0 = time.monotonic()
    b = blame(repo, rev, path, line)
    rc, src = git(repo, "show", f"{rev}:{path}")
    if symbol:
        sym, how = symbol, "given"
    elif rc == 0 and path.endswith(".py"):
        sym, how = pick_symbol(src, line, b.get("text", ""), comment_names(comment))
    else:
        sym, how = None, "not Python"
    tokens = line_tokens(b.get("text", ""))
    port = port_info(b, _read_maps(port_maps or []))
    dev_paths = []
    if port["map"]:
        mapped = (port["map"].get("paths") or {}).get(path)
        dev_paths = [mapped] if mapped else []
    d = dev_search(repo, dev, sym, tokens, dev_paths, file=path)
    dirs = transcript_dirs(transcripts if transcripts is not None else [DEFAULT_TRANSCRIPTS])
    # The function name, and the line's most distinctive name (dotted, or with an underscore or a capital).
    terms_tr = [sym] if sym else []
    for t in tokens:
        if " " not in t and t != sym and ("." in t or "_" in t.strip("_") or t[:1].isupper()):
            terms_tr.append(t)
            break
    tr = {t: search_transcripts(t, dirs) for t in terms_tr}
    # The session that wrote a commit typed its subject; the subject survives restacks, the SHA does not.
    found = [c for c in [d["introduced"], d["file_added"], *[t["first_anywhere"] for t in d["token_commits"]],
                         *[t["first"] for t in d["token_commits"]]] if c]
    sessions = {}
    for c in found:
        term = subject_term(c["subject"])
        if len(sessions) == 3 or not term or term in sessions:
            continue
        sessions[term] = search_transcripts(term, dirs) | {"sha": c["sha"]}
    stem = Path(path).stem
    terms = ([sym] if sym else []) + ([stem] if len(stem) >= 5 else []) + [t for t in tokens if " " not in t]
    return {
        "repo": repo, "rev": rev, "file": path, "line": line,
        "facts": read_facts(facts, terms),
        "blame": b, "port": port, "symbol": sym, "symbol_how": how, "tokens": tokens, "dev": d,
        "transcripts": tr, "commit_sessions": sessions,
        "mr": which_mr(repo, b.get("sha"), mrs or {}),
        "seconds": round(time.monotonic() - t0, 2),
    }


def _c(c: dict | None) -> str:
    return f"{c['sha'][:9]} {c['date'][:10]} {c['subject'][:70]}" if c else "none"


def render(r: dict, label: str | None = None) -> str:
    b = r["blame"]
    out = [f"{label or r['file']}: {r['file']}:{r['line']} @ {r['rev'][:9]}  ({r['seconds']} s)"]
    out.append("Jisu said (fact log): " + ("none" if not r["facts"] else ""))
    out += [f"  {f}" for f in r["facts"]]
    out.append(f"Blame: {b.get('sha', '')[:9] if b.get('sha') else 'none'} {b.get('date', '')[:19]} "
               f"{(b.get('subject') or '')[:70]}")
    out.append(f"  line: {b.get('text', '').strip()[:120]}")
    p = r["port"]
    out.append(f"Port commit: {'yes, ' + p['why'] if p['is_port'] else 'no'}")
    if p["map"]:
        out.append(f"  map sources: {', '.join(s[:9] for s in p['map'].get('sources', []))}")
    if r["mr"]:
        out.append(f"MR holding the blamed commit: !{r['mr']}")
    d = r["dev"]
    out.append(f"Symbol: {r['symbol'] or 'none'} ({r['symbol_how']}); dev line {d['range']} in {', '.join(d['paths']) or '?'}")
    out.append(f"  introduced: {_c(d['introduced'])}")
    if d.get("file_added"):
        out.append(f"  file first added (nothing on the line itself): {_c(d['file_added'])}")
    for c in d["function_commits"]:
        out.append(f"  touched:    {_c(c)}")
    for t in d["token_commits"]:
        out.append(f"  first adds {t['token'][:50]!r}: {_c(t['first'])}" + (f" ({t['hits']} hits)" if t["hits"] > 1 else ""))
        if t.get("first_anywhere"):
            out.append(f"    earlier elsewhere on the dev line: {_c(t['first_anywhere'])}")
    for term, h in r["transcripts"].items():
        out.append(f"Transcripts with {term!r}: {h['count']}")
        for f in h["files"]:
            out.append(f"  {f['last_write']} {f['task'] or f['project'][-50:]}: {f['snippet'][:200]}")
    for term, h in r["commit_sessions"].items():
        out.append(f"Sessions that typed the subject of {h['sha'][:9]} ({term[:40]!r}): {h['count']}")
        for f in h["files"]:
            out.append(f"  {f['last_write']} {f['task'] or f['project'][-50:]}: {f['snippet'][:200]}")
    return "\n".join(out) + "\n"


def _mrs(items: list[str] | None) -> dict[int, str]:
    out = {}
    for it in items or []:
        iid, _, rng = it.partition("=")
        if ".." not in rng:
            raise SystemExit(f"--mr expects IID=BASE..HEAD, got {it!r}")
        out[int(iid.lstrip("!"))] = rng
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--rev")
    ap.add_argument("--file")
    ap.add_argument("--line", type=int)
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--comment", default="", help="the reviewer's note (backticked names pick the function)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--batch", default=None, help="JSON list of lines to trace")
    ap.add_argument("--out", default=None, help="output folder for --batch")
    ap.add_argument("--dev", default=DEFAULT_DEV, help=f"dev line range (default {DEFAULT_DEV})")
    ap.add_argument("--port-map", action="append", default=None, help=f"glob, or none (default {DEFAULT_PORT_MAPS})")
    ap.add_argument("--facts", default=DEFAULT_FACTS)
    ap.add_argument("--transcripts", action="append", default=None, help=f"dirs or globs (default {DEFAULT_TRANSCRIPTS})")
    ap.add_argument("--mr", action="append", help="IID=BASE..HEAD of an MR in the stack (repeat)")
    a = ap.parse_args()
    common = dict(dev=a.dev, transcripts=a.transcripts, facts=a.facts,
                  port_maps=a.port_map or [DEFAULT_PORT_MAPS], mrs=_mrs(a.mr))
    if a.batch:
        if not a.out:
            ap.error("--batch needs --out")
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        results = []
        for i, spec in enumerate(json.loads(Path(a.batch).read_text()), 1):
            r = trace(spec.get("repo", a.repo), spec["rev"], spec["file"], int(spec["line"]),
                      symbol=spec.get("symbol"), comment=spec.get("body", ""), **{**common, "mrs": _mrs(spec.get("mr")) or common["mrs"]})
            label = spec.get("label", f"line{i}")
            results.append({"label": label, **r})
            name = re.sub(r"[^\w.-]+", "_", label)
            (out / f"{i:02d}-{name}.txt").write_text(render(r, label))
            p = "port" if r["port"]["is_port"] else "    "
            print(f"{i:2d} {label:40} {p} {r['seconds']:6.2f} s  intro {_c(r['dev']['introduced'])[:60]}")
        (out / "results.json").write_text(json.dumps(results, indent=1) + "\n")
        print(f"wrote {out / 'results.json'} ({len(results)} lines)")
        return 0
    if not (a.rev and a.file and a.line):
        ap.error("--rev, --file and --line are required (or --batch)")
    r = trace(a.repo, a.rev, a.file, a.line, symbol=a.symbol, comment=a.comment, **common)
    print(json.dumps(r, indent=1) if a.json else render(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
