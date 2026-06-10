"""Tests for astra.service — cross-platform listener lifecycle management."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from astra import service


def _which_only(name, path):
    """shutil.which side_effect that resolves only `name`."""
    return lambda c: path if c == name else None


class TestDetectBackend:
    def test_systemd_when_bus_running(self):
        with patch.object(service.sys, "platform", "linux"), \
             patch.object(service.shutil, "which",
                          side_effect=_which_only("systemctl", "/usr/bin/systemctl")), \
             patch.object(service.subprocess, "run",
                          return_value=MagicMock(returncode=0, stdout="running\n")):
            assert service.detect_backend() == "systemd"

    def test_manual_when_user_bus_down(self):
        """systemctl present but user bus unreachable (e.g. user@.service
        OOM-killed) → manual fallback."""
        with patch.object(service.sys, "platform", "linux"), \
             patch.object(service.shutil, "which",
                          side_effect=_which_only("systemctl", "/usr/bin/systemctl")), \
             patch.object(service.subprocess, "run",
                          return_value=MagicMock(returncode=1,
                                                 stdout="Failed to connect to bus\n")):
            assert service.detect_backend() == "manual"

    def test_manual_when_no_systemctl(self):
        with patch.object(service.sys, "platform", "linux"), \
             patch.object(service.shutil, "which", return_value=None):
            assert service.detect_backend() == "manual"

    def test_launchd_on_darwin_with_plist(self):
        with patch.object(service.sys, "platform", "darwin"), \
             patch.object(service.shutil, "which",
                          side_effect=_which_only("launchctl", "/bin/launchctl")), \
             patch.object(service.os.path, "exists", return_value=True):
            assert service.detect_backend() == "launchd"

    def test_manual_on_darwin_without_plist(self):
        with patch.object(service.sys, "platform", "darwin"), \
             patch.object(service.shutil, "which",
                          side_effect=_which_only("launchctl", "/bin/launchctl")), \
             patch.object(service.os.path, "exists", return_value=False):
            assert service.detect_backend() == "manual"


class TestSystemdEnv:
    def test_fills_missing_xdg_runtime_dir(self):
        env_without = {k: v for k, v in os.environ.items()
                       if k not in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")}
        with patch.object(service.os, "environ", env_without):
            env = service._systemd_env()
        uid = os.getuid()
        assert env["XDG_RUNTIME_DIR"] == f"/run/user/{uid}"
        assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path=/run/user/{uid}/bus"

    def test_preserves_existing_values(self):
        env_with = dict(os.environ)
        env_with["XDG_RUNTIME_DIR"] = "/custom/dir"
        env_with["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/custom/bus"
        with patch.object(service.os, "environ", env_with):
            env = service._systemd_env()
        assert env["XDG_RUNTIME_DIR"] == "/custom/dir"
        assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/custom/bus"


class TestLockHelpers:
    def test_lock_pid_reads_pid(self, tmp_path):
        lock = tmp_path / "lock"
        lock.write_text("12345")
        with patch.object(service, "LOCK_FILE", str(lock)):
            assert service._lock_pid() == 12345

    def test_lock_pid_none_when_missing(self, tmp_path):
        with patch.object(service, "LOCK_FILE", str(tmp_path / "nope")):
            assert service._lock_pid() is None

    def test_lock_pid_none_when_empty(self, tmp_path):
        lock = tmp_path / "lock"
        lock.write_text("")
        with patch.object(service, "LOCK_FILE", str(lock)):
            assert service._lock_pid() is None

    def test_pid_alive_self(self):
        assert service._pid_alive(os.getpid()) is True

    def test_pid_alive_dead(self):
        # PID near the 4M default pid_max is almost certainly not running
        assert service._pid_alive(4194000) is False

    def test_cleanup_stale_lock_removes_dead(self, tmp_path):
        lock = tmp_path / "lock"
        lock.write_text("4194000")  # dead pid
        with patch.object(service, "LOCK_FILE", str(lock)):
            assert service._cleanup_stale_lock() is True
        assert not lock.exists()

    def test_cleanup_keeps_live_lock(self, tmp_path):
        lock = tmp_path / "lock"
        lock.write_text(str(os.getpid()))  # our own pid — alive
        with patch.object(service, "LOCK_FILE", str(lock)):
            assert service._cleanup_stale_lock() is False
        assert lock.exists()


class TestReadLogs:
    def test_journalctl_used_when_available(self):
        with patch.object(service.shutil, "which",
                          return_value="/usr/bin/journalctl"), \
             patch.object(service.subprocess, "run",
                          return_value=MagicMock(returncode=0,
                                                 stdout="line1\nline2\n")):
            assert service.read_logs(5) == "line1\nline2\n"

    def test_file_fallback_when_no_journalctl(self, tmp_path):
        log = tmp_path / "astra.log"
        log.write_text("a\nb\nc\nd\n")
        with patch.object(service.shutil, "which", return_value=None), \
             patch.object(service, "MAC_LOG_FILE", str(log)):
            assert service.read_logs(2) == "c\nd\n"

    def test_none_when_nothing_available(self, tmp_path):
        with patch.object(service.shutil, "which", return_value=None), \
             patch.object(service, "MAC_LOG_FILE", str(tmp_path / "no1")), \
             patch.object(service.os.path, "exists", return_value=False):
            assert service.read_logs(5) is None


class TestGenerateLaunchdPlist:
    def test_contains_label_and_paths(self):
        with patch.object(service.shutil, "which",
                          return_value="/opt/homebrew/bin/pixi"):
            plist = service.generate_launchd_plist("/Users/me/.dotfiles")
        assert "io.astra.listener" in plist
        assert "/opt/homebrew/bin/pixi" in plist
        assert "/Users/me/.dotfiles/scripts/astra/pixi.toml" in plist
        assert "listen" in plist
        assert "StandardOutPath" in plist
        assert "RunAtLoad" in plist

    def test_valid_xml(self):
        import xml.etree.ElementTree as ET
        with patch.object(service.shutil, "which", return_value="/usr/bin/pixi"):
            plist = service.generate_launchd_plist("/home/x/.dotfiles")
        ET.fromstring(plist)  # should parse without error


class TestRestartFlow:
    def test_systemd_restart_invokes_systemctl(self):
        with patch.object(service, "detect_backend", return_value="systemd"), \
             patch.object(service, "_cleanup_stale_lock", return_value=False), \
             patch.object(service.subprocess, "run",
                          return_value=MagicMock(returncode=0)) as run:
            rc = service.restart()
        assert rc == 0
        args = run.call_args[0][0]
        assert args[:3] == ["systemctl", "--user", "restart"]

    def test_launchd_restart_invokes_kickstart(self):
        with patch.object(service, "detect_backend", return_value="launchd"), \
             patch.object(service, "_cleanup_stale_lock", return_value=False), \
             patch.object(service.subprocess, "run",
                          return_value=MagicMock(returncode=0)) as run:
            rc = service.restart()
        assert rc == 0
        args = run.call_args[0][0]
        assert args[0] == "launchctl"
        assert "kickstart" in args
        assert "-k" in args

    def test_manual_restart_stops_then_starts(self):
        with patch.object(service, "detect_backend", return_value="manual"), \
             patch.object(service, "_cleanup_stale_lock", return_value=False), \
             patch.object(service, "stop", return_value=0) as stop, \
             patch.object(service, "start", return_value=0) as start:
            rc = service.restart()
        assert rc == 0
        stop.assert_called_once()
        start.assert_called_once()
