"""Tests for _fire_and_forget and bare-wid god mode matching (0.16.4)."""
import os
import threading

import pytest

import astra
from astra import telegram


# -- _fire_and_forget --------------------------------------------------------

def test_fire_and_forget_runs_target():
    event = threading.Event()
    telegram._fire_and_forget(lambda: event.set())
    assert event.wait(timeout=2)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_fire_and_forget_exceptions_dont_propagate():
    event = threading.Event()

    def _bad():
        event.set()
        raise RuntimeError("boom")

    telegram._fire_and_forget(_bad)
    assert event.wait(timeout=2)
    # Let the thread fully terminate so the exception is caught by this test's
    # filterwarnings marker, not leaked into the next test.
    import time
    time.sleep(0.05)


# -- bare-wid god mode matching ----------------------------------------------

@pytest.fixture()
def god_mode_dir(tmp_path):
    sig_dir = str(tmp_path / "signals")
    os.makedirs(sig_dir, exist_ok=True)
    orig_sig = astra.config.SIGNAL_DIR
    orig_god = astra.config.GOD_MODE_PATH
    astra.config.SIGNAL_DIR = sig_dir
    astra.config.GOD_MODE_PATH = os.path.join(sig_dir, "_god_mode.json")
    yield
    astra.config.SIGNAL_DIR = orig_sig
    astra.config.GOD_MODE_PATH = orig_god


def test_bare_wid_matches_suffixed(god_mode_dir):
    """Bare 'w4' should match when 'w4a' is in god mode."""
    astra._set_god_mode("4", True)
    assert astra._is_god_mode_for("w4")
    assert not astra._is_god_mode_for("w5")


def test_bare_wid_no_false_prefix_match(god_mode_dir):
    """'w4' should not match 'w40a' — only 'w4' + single-letter suffix."""
    astra._set_god_mode("40", True)
    assert not astra._is_god_mode_for("w4")
    assert astra._is_god_mode_for("w40")
