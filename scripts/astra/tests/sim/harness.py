"""Simulation harness that wires fakes together and drives _listen_tick."""
import json
import os
import re
import subprocess
import tempfile
from unittest.mock import patch, MagicMock

from astra import listener, config
from .fake_clock import FakeClock
from .fake_telegram import FakeTelegram
from .fake_tmux import FakeTmux


class SimulationHarness:
    """Wires FakeTelegram, FakeTmux, and FakeClock together and patches
    all astra modules so ``_listen_tick`` runs against fakes.
    """

    def __init__(self):
        self.tg = FakeTelegram()
        self.tmux = FakeTmux()
        self.clock = FakeClock()
        self.subprocess_calls: list = []
        self._patches: list = []
        self._signal_dir: str = ""
        self._tmpdir = None

    def setup(self):
        """Apply all patches.  Call in setUp() or before first tick."""
        self._tmpdir = tempfile.mkdtemp(prefix="astra_sim_")
        self._signal_dir = os.path.join(self._tmpdir, "signals")
        os.makedirs(self._signal_dir, exist_ok=True)

        # --- Patch config ---
        self._patches.append(patch.object(config, "SIGNAL_DIR", self._signal_dir))
        self._patches.append(patch.object(config, "CHAT_ID", "123"))
        self._patches.append(patch.object(config, "BOT", "fake_bot_token"))

        # --- Patch time in all modules that use it during _listen_tick ---
        fake_time = self._make_time_module()
        for mod_path in ("astra.listener", "astra.signals", "astra.routing",
                         "astra.commands", "astra.state"):
            self._patches.append(patch(f"{mod_path}.time", fake_time))

        # --- Patch telegram I/O on the listener module's reference ---
        tg_methods = [
            "tg_send", "_poll_updates", "_answer_callback_query",
            "_remove_inline_keyboard", "_download_tg_file", "_set_bot_commands",
            "_extract_chat_messages", "_build_inline_keyboard",
            "_build_reply_keyboard", "_send_long_message",
        ]
        import astra.telegram as tg_mod
        for name in tg_methods:
            self._patches.append(
                patch.object(tg_mod, name, getattr(self.tg, name))
            )

        # --- Patch tmux I/O on the tmux module ---
        tmux_methods = [
            "scan_claude_sessions", "scan_cli_sessions",
            "_capture_pane", "_capture_pane_ansi",
            "_get_pane_width", "_get_cursor_x", "_get_pane_command",
            "_get_locally_viewed_windows", "_join_wrapped_lines",
            "format_sessions_message", "_sessions_keyboard",
        ]
        import astra.tmux as tmux_mod
        for name in tmux_methods:
            self._patches.append(
                patch.object(tmux_mod, name, getattr(self.tmux, name))
            )

        # --- Patch subprocess.run in all modules that call it ---
        for mod_path in ("astra.listener", "astra.routing", "astra.commands"):
            self._patches.append(
                patch(f"{mod_path}.subprocess.run", side_effect=self._fake_subprocess_run)
            )

        # --- Patch time.sleep in signals module (it sleeps before pane capture) ---
        self._patches.append(patch("astra.signals.time", self._make_time_module()))

        # --- Disable auto-reload ---
        self._patches.append(
            patch.object(listener, "_check_file_changes", return_value=False)
        )

        # --- Patch state functions that touch the filesystem ---
        # Most state functions use SIGNAL_DIR which we already redirected.
        # But _is_local_suppress_enabled reads a file — default to off for simplicity.
        import astra.state as state_mod
        self._patches.append(
            patch.object(state_mod, "_is_local_suppress_enabled", return_value=False)
        )
        self._patches.append(
            patch.object(state_mod, "_is_silent", return_value=True)
        )
        self._patches.append(
            patch.object(state_mod, "_god_mode_wids", return_value=[])
        )
        self._patches.append(
            patch.object(state_mod, "_is_god_mode_for", return_value=False)
        )
        self._patches.append(
            patch.object(state_mod, "_is_autofocus_enabled", return_value=True)
        )

        # Start all patches
        for p in self._patches:
            p.start()

    def teardown(self):
        """Stop all patches and clean up temp files."""
        for p in reversed(self._patches):
            p.stop()
        self._patches.clear()
        # Clean up temp dir
        import shutil
        if self._tmpdir and os.path.isdir(self._tmpdir):
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_time_module(self):
        """Create a fake time module with our clock."""
        fake = MagicMock()
        fake.time = self.clock.time
        fake.sleep = self.clock.sleep
        return fake

    def _fake_subprocess_run(self, args, **kwargs):
        """Record subprocess calls and return success."""
        self.subprocess_calls.append({"args": args, "kwargs": kwargs})
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    def make_listener_state(self, **overrides):
        """Create a pre-initialized _ListenerState with sessions from FakeTmux."""
        sessions = self.tmux.scan_claude_sessions()
        defaults = {
            "sessions": sessions,
            "last_scan": self.clock.time(),
            "offset": 0,
        }
        defaults.update(overrides)
        return listener._ListenerState(**defaults)

    def tick(self, s):
        """Execute one ``_listen_tick`` and return its result."""
        return listener._listen_tick(s)

    def run_ticks(self, s, n, advance_between=0.5):
        """Execute *n* ticks, advancing the clock between each.

        Returns list of tick results.
        """
        results = []
        for i in range(n):
            result = listener._listen_tick(s)
            results.append(result)
            if result in ("quit", "pause_break"):
                break
            if i < n - 1:
                self.clock.advance(advance_between)
        return results

    def inject_signal(self, event, wid, **extra):
        """Write a signal file to the temp signal dir."""
        import time as _real_time
        sig = {"event": event, "wid": wid}
        sig.update(extra)
        fname = f"sig_{event}_{wid}_{_real_time.time():.6f}.json"
        path = os.path.join(self._signal_dir, fname)
        with open(path, "w") as f:
            json.dump(sig, f)
        return path

    # --- Assertions ---

    def assert_sent(self, pattern):
        """Assert at least one sent message matches the regex."""
        matches = self.tg.find_sent(pattern)
        if not matches:
            texts = [m["text"][:100] for m in self.tg.sent_messages]
            raise AssertionError(
                f"No sent message matches /{pattern}/. "
                f"Sent ({len(self.tg.sent_messages)}): {texts}"
            )
        return matches

    def assert_not_sent(self, pattern):
        """Assert no sent message matches the regex."""
        matches = self.tg.find_sent(pattern)
        if matches:
            raise AssertionError(
                f"Found unexpected message matching /{pattern}/: "
                f"{matches[0]['text'][:200]}"
            )

    def assert_keys_sent_to(self, pane_target):
        """Assert that at least one subprocess call targeted the given pane."""
        for call in self.subprocess_calls:
            args = call["args"]
            if isinstance(args, list) and len(args) >= 3:
                cmd_str = args[2] if args[0] == "bash" else str(args)
                if pane_target in cmd_str and "send-keys" in cmd_str:
                    return True
        panes = [str(c["args"]) for c in self.subprocess_calls]
        raise AssertionError(
            f"No send-keys call to pane {pane_target}. "
            f"Calls: {panes}"
        )

    def dump_timeline(self):
        """Return human-readable debug output of all events."""
        lines = []
        for i, m in enumerate(self.tg.sent_messages):
            lines.append(f"[tg {i}] msg_id={m['msg_id']}: {m['text'][:120]}")
        for i, c in enumerate(self.subprocess_calls):
            lines.append(f"[sp {i}] {c['args']}")
        return "\n".join(lines)
