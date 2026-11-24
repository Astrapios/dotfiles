"""The repo map of mr_overview.py lays out its labels with no overlap and nothing outside its box (T-y5 on !806).

On !806 the two test files' names ran under their "N new tests, M changed, +a -b" lines, and a long method chip
(`MultistaticSimulatedPhaseHistoryData.compute_from_components`) ran past its file box. The facts here are made by
hand: two sibling test files with the same kind of long names and counts, and a file with that long chip.

Geometry: every <text> gets a box from its x, y, anchor and the script's own width estimate `tw` (the estimate the
drawing is laid out with; the screenshots in the run page check it against real fonts).

Run: pixi run --manifest-path <site3d>/pyproject.toml -e test python -m pytest scripts/claude/skills/mr-review/tests
"""

from __future__ import annotations

import importlib.util
import itertools
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

OVERVIEW = Path(__file__).resolve().parent.parent / "scripts" / "mr_overview.py"
NS = "{http://www.w3.org/2000/svg}"


@pytest.fixture(scope="module")
def mo():
    spec = importlib.util.spec_from_file_location("mr_overview_map", OVERVIEW)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mr_overview_map"] = mod
    spec.loader.exec_module(mod)
    return mod


def sym(q: str, status: str, kind: str = "function") -> dict:
    return {"qualname": q, "status": status, "kind": kind, "line": 1, "line_to": 2}


def a_test_file(path: str, added: int, changed: int, unchanged: int, plus: int, minus: int) -> dict:
    syms = ([sym(f"test_new_{i}", "added") for i in range(added)]
            + [sym(f"test_changed_{i}", "changed") for i in range(changed)]
            + [sym(f"_helper_{i}", "unchanged") for i in range(unchanged)])
    return {"path": path, "status": "changed", "plus": plus, "minus": minus, "symbols": syms}


@pytest.fixture(scope="module")
def fx() -> dict:
    files = [
        a_test_file("image_formation/tests/test_backprojection.py", 6, 12, 47, 349, 58),
        a_test_file("image_formation/tests/test_geometry.py", 5, 4, 24, 117, 11),
        {"path": "sar_simulation/phase_history/phase_history.py", "status": "changed", "plus": 10, "minus": 33,
         "symbols": [sym("PhaseHistoryGridProcessor.process", "changed", "method"),
                     sym("MultistaticSimulatedPhaseHistoryData.compute_from_components", "changed", "method"),
                     sym("SimulationMethod", "unchanged")]},
    ]
    return {"files": files, "top_level": ["image_formation", "sar_simulation", "python_common"],
            "siblings": {"image_formation/tests": ["test_backprojection.py", "test_geometry.py", "test_pfa.py"],
                         "sar_simulation/phase_history": ["phase_history.py", "noise.py"]}}


def text_box(mo, t: ET.Element) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) of a <text>: the cap height above the baseline, descenders below, `tw` wide."""
    size = float(t.get("font-size"))
    mono = "monospace" in t.get("font-family", "")
    bold = int(t.get("font-weight", "400")) >= 600
    w = mo.tw(t.text or "", size, mono, bold)
    x, y = float(t.get("x")), float(t.get("y"))
    x0 = {"start": x, "middle": x - w / 2, "end": x - w}[t.get("text-anchor", "start")]
    return x0, y - 0.75 * size, x0 + w, y + 0.22 * size


def rect_box(r: ET.Element) -> tuple[float, float, float, float]:
    x, y = float(r.get("x")), float(r.get("y"))
    return x, y, x + float(r.get("width")), y + float(r.get("height"))


def overlap(a, b, gap: float = 1.0) -> bool:
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] and b[1] < a[3]


def inside(a, outer, pad: float = 2.0) -> bool:
    return a[0] >= outer[0] + pad and a[2] <= outer[2] - pad and a[1] >= outer[1] and a[3] <= outer[3]


def test_no_label_overlaps_and_every_label_stays_in_its_box(mo, fx):
    svg = mo.repo_map_svg(fx, mo.boxes_of(fx), root="site3d")
    root = ET.fromstring(svg)
    width = float(root.get("viewBox").split()[2])
    texts = [(t.text, text_box(mo, t)) for t in root.iter(f"{NS}text")]
    assert any(s == "test_backprojection.py" for s, _ in texts) and any(s == "test_geometry.py" for s, _ in texts)

    clashes = [(a, b) for (a, ba), (b, bb) in itertools.combinations(texts, 2) if overlap(ba, bb)]
    assert clashes == []
    off = [s for s, b in texts if b[0] < 0 or b[2] > width]
    assert off == []

    # each box (a file or a chip) holds its own labels; each chip sits inside its file's box
    groups = {g.get("data-box"): g for g in root.iter(f"{NS}g") if g.get("data-box")}
    for bid, g in groups.items():
        r = rect_box(g.find(f"{NS}rect"))
        for t in g.iter(f"{NS}text"):
            assert inside(text_box(mo, t), r), (bid, t.text)
        if "::" in bid:
            parent = rect_box(groups[bid.split("::")[0]].find(f"{NS}rect"))
            assert inside(r, parent, pad=0), bid


def test_long_chip_name_keeps_every_character(mo, fx):
    svg = mo.repo_map_svg(fx, mo.boxes_of(fx), root="site3d")
    root = ET.fromstring(svg)
    g = next(g for g in root.iter(f"{NS}g")
             if g.get("data-box", "").endswith("::MultistaticSimulatedPhaseHistoryData.compute_from_components"))
    assert "".join(t.text for t in g.iter(f"{NS}text")) == "MultistaticSimulatedPhaseHistoryData.compute_from_components"


def test_test_file_counts_are_all_shown(mo, fx):
    svg = mo.repo_map_svg(fx, mo.boxes_of(fx), root="site3d")
    root = ET.fromstring(svg)
    g = next(g for g in root.iter(f"{NS}g") if g.get("data-box") == "image_formation/tests/test_backprojection.py")
    words = " ".join(t.text for t in g.iter(f"{NS}text"))
    assert "6 new tests" in words and "12 changed" in words and "+349 -58" in words
