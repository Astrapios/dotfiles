"""A function or method handed to a worker pool counts as used: "passed to <callee> in <caller>" (T-y5 on !806).

On !806 both grid processors' `process` showed "Called from none": an instance goes to `multiprocess_grid`, which
hands it to `multiprocess_items`, which gives `item_processor.process` to `pool.imap`. Nothing calls `process` by
name with a receiver of the subclass's type. The repo here has the same shape (a base processor, a grid runner that
passes the processor on to an item runner that maps its `process`), plus a function given to `map`, `submit` and a
`key=` callback, a bound method given to `map`, an instance passed to calls that never use `process`, and a method
nobody uses at all.

Run: pixi run --manifest-path <site3d>/pyproject.toml -e test python -m pytest scripts/claude/skills/mr-review/tests
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

OVERVIEW = Path(__file__).resolve().parent.parent / "scripts" / "mr_overview.py"

POOL = '''"""Worker pools."""

from concurrent.futures import ThreadPoolExecutor
from multiprocessing.dummy import Pool


class ItemProcessor:
    """Processes one item."""

    def process(self, item):
        raise NotImplementedError


class GridProcessor(ItemProcessor):
    """Processes one grid range."""


def run_items(processor: ItemProcessor, items):
    with Pool(2) as pool:
        return list(pool.imap(processor.process, items))


def run_grid(cfg, grid_processor: GridProcessor, n):
    return run_items(grid_processor, range(n))


def store(x):
    return [x]


def submit_all(fn, xs):
    with ThreadPoolExecutor() as ex:
        return [ex.submit(fn, x) for x in xs]
'''


def work(k: int) -> str:
    """The MR changes the bodies of `process`, `unused` and `scale` (k)."""
    return f'''"""Work."""

from concurrent.futures import ThreadPoolExecutor

from lib.pool import GridProcessor, run_grid, store


class SquareProcessor(GridProcessor):
    """Squares an item."""

    def __init__(self, k):
        self.k = k

    def process(self, item):
        return item * item * self.k * {k}

    def unused(self):
        return {k}


def scale(x):
    return {k} * x


def runs():
    return run_grid(None, grid_processor=SquareProcessor(2), n=4)


def runs_var():
    p = SquareProcessor(3)
    return run_grid(None, p, 4)


def mapped(xs):
    with ThreadPoolExecutor() as ex:
        ex.submit(scale, 3)
    return list(map(scale, xs))


def callback(xs):
    return sorted(xs, key=scale)


def bound(xs):
    p = SquareProcessor(1)
    return list(map(p.process, xs))


def not_used(xs):
    p = SquareProcessor(1)
    print(p)
    return store(p)
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> dict:
    r = tmp_path_factory.mktemp("pool_callers")
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "lib").mkdir()
    (r / "lib/pool.py").write_text(POOL)
    (r / "lib/work.py").write_text(work(1))
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    base = _git(r, "rev-parse", "HEAD")
    (r / "lib/work.py").write_text(work(2))
    _git(r, "commit", "-q", "-am", "mr")
    return {"path": str(r), "base": base, "head": _git(r, "rev-parse", "HEAD")}


@pytest.fixture(scope="module")
def mo():
    spec = importlib.util.spec_from_file_location("mr_overview_pool", OVERVIEW)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mr_overview_pool"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fx(mo, repo) -> dict:
    return mo.facts(repo["path"], repo["base"], repo["head"])


def sym(fx: dict, q: str) -> dict:
    f = next(f for f in fx["files"] if f["path"] == "lib/work.py")
    return next(s for s in f["symbols"] if s["qualname"] == q)


def line_of(needle: str) -> int:
    hits = [i for i, ln in enumerate(work(2).splitlines(), 1) if needle in ln]
    assert len(hits) == 1, needle
    return hits[0]


def passed(s: dict) -> list[tuple[int, str, str]]:
    return sorted((c["line"], c["callee"], c["caller"]) for c in s["call_sites"] if c["how"] == "passed")


def test_instance_handed_to_a_pool_runner_counts_for_the_method_it_runs(fx):
    s = sym(fx, "SquareProcessor.process")
    assert passed(s) == [
        (line_of("grid_processor=SquareProcessor(2)"), "run_grid", "runs"),
        (line_of("return run_grid(None, p, 4)"), "run_grid", "runs_var"),
        (line_of("map(p.process, xs)"), "map", "bound"),
    ]
    # `print(p)` and `store(p)` take the instance but never run its process: not listed
    assert all(c["caller"] != "not_used" for c in s["call_sites"] + s["possible_sites"])


def test_function_handed_to_map_submit_and_a_callback_keyword(fx):
    s = sym(fx, "scale")
    assert passed(s) == [
        (line_of("ex.submit(scale, 3)"), "ex.submit", "mapped"),
        (line_of("list(map(scale, xs))"), "map", "mapped"),
        (line_of("key=scale"), "sorted", "callback"),
    ]


def test_card_says_passed_to_and_no_caller_found(mo, fx):
    cards = mo.fragment_parts(fx, mo.boxes_of(fx), {}, "https://example.com/g/repo")["cards"]
    process = cards[cards.index('data-box="lib/work.py::SquareProcessor.process"'):]
    process = process[:process.index("</div>")]
    assert "passed to <code>run_grid</code> in <code>runs</code>" in process
    assert "work.py:" + str(line_of("grid_processor=SquareProcessor(2)")) in process
    unused = cards[cards.index('data-box="lib/work.py::SquareProcessor.unused"'):]
    unused = unused[:unused.index("</div>")]
    assert "no caller found in the repo" in unused
    assert ">none" not in cards and " none outside tests" not in cards


def test_passed_uses_count_as_used_outside_the_file_in_the_pipeline_list(mo, fx):
    # scale and process are used only inside work.py; the pipeline list keeps them under "only inside"
    note = mo.fragment_parts(fx, mo.boxes_of(fx), {}, "https://example.com/g/repo")["pipe_note"]
    assert "Used only inside <code>work.py</code>" in note
