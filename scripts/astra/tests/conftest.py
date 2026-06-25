"""Shared pytest fixtures.

Crucially: isolate every test from the live signal dir and god-mode file.

Many unit tests call ``save_active_prompt`` / ``_save_*focus_state`` /
``_mark_busy`` / ``_set_god_mode`` etc. Without isolation these write into
the real ``/tmp/astra_signals`` and ``~/.config/astra_god_mode.json``,
which (a) leaks state between tests and (b) pollutes a *live* listener —
e.g. a stale ``_active_prompt_w4.json`` made ``_is_active_question_prompt``
suppress real permission notifications for window 4, and a stale
``_smartfocus.json`` leaked a 👁‍🗨 icon into a /status assertion.

This autouse fixture redirects both paths to a per-test temp dir by
default. Tests that manage ``config.SIGNAL_DIR`` themselves (e.g. via
their own setUp, or the simulation harness) still override it for their
own scope — this just guarantees the default is never the real dir.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from astra import config  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_runtime_state(tmp_path, monkeypatch):
    sig = tmp_path / "signals"
    sig.mkdir()
    monkeypatch.setattr(config, "SIGNAL_DIR", str(sig))
    monkeypatch.setattr(config, "GOD_MODE_PATH", str(sig / "_god_mode.json"))
    yield
