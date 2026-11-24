"""Call sites in mr_overview.py facts resolve through `ast`, not by bare name (T-xI on !816, T-xJ on !782).

A small git repo made in tmp_path: two script modules that each define `main` and `print_run_summary` (the two
Krieger scripts of !816), an `Antenna` class with a property, a classmethod and a method, a `Platform` dataclass with
an `antenna` field, a second class with a same-name method, and callers that reach them through an aliased import,
a module alias, an annotated receiver, a constructor, a classmethod's return annotation, a dataclass field, and a
receiver whose type cannot be known.

Run: pixi run --manifest-path <site3d>/pyproject.toml -e test python -m pytest scripts/claude/skills/mr-review/tests
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

OVERVIEW = Path(__file__).resolve().parent.parent / "scripts" / "mr_overview.py"


def script(tag: str, n: int) -> str:
    """One Krieger-like script; `n` is the value its print_run_summary adds (changed by the MR)."""
    return f'''"""Script {tag}."""


def print_run_summary(x):
    print("{tag}", x + {n})


def main():
    print_run_summary({n})


if __name__ == "__main__":
    main()
'''


ANTENNA_BASE = '''"""Antennas."""


class Antenna:
    """An antenna."""

    def __init__(self, size):
        self.size = size

    @property
    def peak_gain_dbi(self) -> float:
        return self.size * 1.0

    @classmethod
    def square(cls, size) -> "Antenna":
        return cls(size)

    def gain(self, angle):
        return self.size - angle


class Other:
    """Not an antenna, but it has a gain too."""

    def gain(self, angle):
        return angle

    @property
    def peak_gain_dbi(self):
        return 0.0
'''

ANTENNA_HEAD = (ANTENNA_BASE.replace("self.size * 1.0", "self.size * 2.0")
                .replace("return cls(size)", "return cls(size + 0)")
                .replace("return self.size - angle", "return self.size - 2 * angle")
                + '''

def make_antenna(size):
    return Antenna(size)


def default_antenna():
    return make_antenna(1)
''')

PLATFORM = '''"""Platforms."""

from dataclasses import dataclass

from lib.antenna import Antenna


@dataclass
class Platform:
    """A platform carrying an antenna."""

    antenna: Antenna
    name: str = "p"

    @property
    def main_antenna(self) -> Antenna:
        return self.antenna


class Drone(Platform):
    """A platform subclass: its main_antenna comes from Platform."""
'''

USERS = '''"""Callers of the antenna and the scripts."""

import lib.antenna as la
from app import krieger_a as ka
from app.krieger_b import print_run_summary as prs
from lib.antenna import Antenna as Ant
from lib.antenna import Other
from lib.platform import Drone, Platform


def aliased():
    a = Ant.square(2)
    b = la.Antenna.square(3)
    return a, b


def annotated(ant: Ant):
    return ant.gain(1)


def optional(ant: Ant | None = None):
    return ant.gain(6)


def constructed():
    a = Ant(4)
    return a.gain(4)


def returned():
    b = Ant.square(5)
    return b.gain(5)


def through_field(p: Platform):
    return p.antenna.peak_gain_dbi


def through_property(d: Drone):
    return d.main_antenna.peak_gain_dbi


def read_property(ant: "Ant"):
    return ant.peak_gain_dbi


def unknown(x):
    return x.gain(2), x.peak_gain_dbi


def other(o: Other):
    return o.gain(3), o.peak_gain_dbi


def run_scripts():
    ka.main()
    prs(7)


def main():
    run_scripts()
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> dict:
    r = tmp_path_factory.mktemp("callers")
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    for d in ("app", "lib", "use"):
        (r / d).mkdir()
    (r / "app/krieger_a.py").write_text(script("a", 1))
    (r / "app/krieger_b.py").write_text(script("b", 1))
    (r / "lib/antenna.py").write_text(ANTENNA_BASE)
    (r / "lib/platform.py").write_text(PLATFORM)
    (r / "use/users.py").write_text(USERS)
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    base = _git(r, "rev-parse", "HEAD")
    _git(r, "checkout", "-q", "-b", "mr")
    (r / "app/krieger_a.py").write_text(script("a", 2))
    (r / "app/krieger_b.py").write_text(script("b", 2))
    (r / "lib/antenna.py").write_text(ANTENNA_HEAD)
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "mr")
    return {"path": str(r), "base": base, "head": _git(r, "rev-parse", "HEAD")}


@pytest.fixture(scope="module")
def mo():
    spec = importlib.util.spec_from_file_location("mr_overview_callers", OVERVIEW)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mr_overview_callers"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fx(mo, repo) -> dict:
    return mo.facts(repo["path"], repo["base"], repo["head"])


def sym(fx: dict, path: str, q: str) -> dict:
    f = next(f for f in fx["files"] if f["path"] == path)
    return next(s for s in f["symbols"] if s["qualname"] == q)


def lines(src: str, needle: str) -> list[int]:
    return [i for i, ln in enumerate(src.splitlines(), 1) if needle in ln]


def sites(s: dict, key: str = "call_sites") -> list[tuple[str, int]]:
    return sorted((c["path"], c["line"]) for c in s.get(key, []))


def test_same_name_scripts_do_not_cross_list(fx):
    a = sym(fx, "app/krieger_a.py", "print_run_summary")
    b = sym(fx, "app/krieger_b.py", "print_run_summary")
    in_main = lines(script("a", 2), "    print_run_summary(")
    # each script's own main calls its own print_run_summary; the users module calls krieger_b's through an alias
    assert sites(a) == [("app/krieger_a.py", n) for n in in_main]
    assert sites(b) == sorted([("app/krieger_b.py", n) for n in in_main]
                              + [("use/users.py", n) for n in lines(USERS, "prs(7)")])
    assert a["possible_sites"] == [] and b["possible_sites"] == []


def test_main_lists_only_its_own_module(fx):
    a = sym(fx, "app/krieger_a.py", "main")
    b = sym(fx, "app/krieger_b.py", "main")
    guard = lines(script("a", 2), "    main()")
    assert sites(a) == sorted([("app/krieger_a.py", n) for n in guard]
                              + [("use/users.py", n) for n in lines(USERS, "ka.main()")])
    assert sites(b) == [("app/krieger_b.py", n) for n in guard]


def test_classmethod_through_aliased_import_and_module_alias(fx):
    s = sym(fx, "lib/antenna.py", "Antenna.square")
    want = lines(USERS, "Ant.square(") + lines(USERS, "la.Antenna.square(")
    assert sites(s) == sorted(("use/users.py", n) for n in set(want))
    assert all(c["how"] == "call" for c in s["call_sites"])


def test_method_through_annotated_constructed_and_returned_receivers(fx):
    s = sym(fx, "lib/antenna.py", "Antenna.gain")
    want = (lines(USERS, "ant.gain(1)") + lines(USERS, "ant.gain(6)") + lines(USERS, "a.gain(4)")
            + lines(USERS, "b.gain(5)"))
    assert sites(s) == sorted(("use/users.py", n) for n in want)
    # Other.gain through an annotated receiver is another class's method: never listed
    other = lines(USERS, "o.gain(3)")[0]
    assert ("use/users.py", other) not in sites(s) + sites(s, "possible_sites")


def test_unresolved_receiver_is_possible_not_mixed_in(fx):
    s = sym(fx, "lib/antenna.py", "Antenna.gain")
    assert sites(s, "possible_sites") == [("use/users.py", n) for n in lines(USERS, "x.gain(2)")]
    p = sym(fx, "lib/antenna.py", "Antenna.peak_gain_dbi")
    assert sites(p, "possible_sites") == [("use/users.py", n) for n in lines(USERS, "x.peak_gain_dbi")]


def test_property_read_sites(fx):
    p = sym(fx, "lib/antenna.py", "Antenna.peak_gain_dbi")
    assert p["property"] is True
    want = (lines(USERS, "p.antenna.peak_gain_dbi") + lines(USERS, "return ant.peak_gain_dbi")
            + lines(USERS, "d.main_antenna.peak_gain_dbi"))
    assert sites(p) == sorted(("use/users.py", n) for n in want)
    assert {c["how"] for c in p["call_sites"]} == {"read"}


def test_bare_call_inside_the_defining_module(fx):
    s = sym(fx, "lib/antenna.py", "make_antenna")
    assert sites(s) == [("lib/antenna.py", n) for n in lines(ANTENNA_HEAD, "return make_antenna(1)")]


def test_card_and_pipeline_use_resolved_sites(mo, fx):
    parts = mo.fragment_parts(fx, mo.boxes_of(fx), {"boxes": {}}, "https://example.com/p/x")
    assert "(by name)" not in parts["cards"] + parts["pipe_note"]
    cards = parts["cards"]
    card_a = cards[cards.index('id="card-app-krieger-a-py-print-run-summary"'):]
    card_a = card_a[: card_a.index("</div>")]
    assert "krieger_b.py" not in card_a
    card_p = cards[cards.index('id="card-lib-antenna-py-antenna-peak-gain-dbi"'):]
    card_p = card_p[: card_p.index("</div>")]
    assert "<b>Read from</b>" in card_p and "users.py" in card_p
    assert "Possible (unresolved receiver)" in card_p
    # section 3 lists only resolved sites in other files: krieger_b's print_run_summary is called from users.py
    # alone, and krieger_a's only inside its own script
    items = parts["pipe_note"].split("<li>")
    prs = [li for li in items if li.startswith("<code>print_run_summary</code>: called from")]
    assert len(prs) == 1 and "use/users.py" in prs[0] and "krieger_" not in prs[0]
    assert any(li.startswith("<code>Antenna.peak_gain_dbi</code>: read from") and "use/users.py" in li for li in items)
    # Antenna.gain's unresolved receiver (users.py `x.gain(2)`) is on its card, never in section 3
    x_line = lines(USERS, "x.gain(2)")[0]
    assert f"users.py#L{x_line}" not in parts["pipe_note"]
    card_g = cards[cards.index('id="card-lib-antenna-py-antenna-gain"'):]
    assert f"users.py#L{x_line}" in card_g[: card_g.index("</div>")]
