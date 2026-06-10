"""Listener daemon lifecycle management — cross-platform.

`astra service <start|stop|restart|status|log>` abstracts over:

- **systemd** (Linux): `systemctl --user`, with the XDG_RUNTIME_DIR /
  DBUS_SESSION_BUS_ADDRESS env automatically filled in when missing
  (non-login shells don't get them from pam_systemd).
- **launchd** (macOS): `launchctl` against a per-user LaunchAgent
  labelled `io.astra.listener`.
- **manual** (fallback): direct process management via the lock file
  and `nohup` when no service manager is usable (e.g. systemd user
  bus is down — has happened after OOM kills of user@.service).

`restart` also performs cleanups: removes a stale lock file if its PID
is dead, so a crashed listener doesn't block the next start.
"""
from __future__ import annotations

import os
import shutil
import signal as _signal
import subprocess
import sys
import time

LOCK_FILE = "/tmp/astra_listener.lock"
LAUNCHD_LABEL = "io.astra.listener"
LAUNCHD_PLIST = os.path.expanduser(
    f"~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist")
# launchd has no journal; the plist routes stdout/stderr here and the
# /log command falls back to it when journalctl is unavailable.
MAC_LOG_FILE = os.path.expanduser("~/Library/Logs/astra.log")


def _systemd_env() -> dict:
    """Env for systemctl --user. Fills XDG_RUNTIME_DIR / DBUS bus path
    when absent — non-login shells (Claude Code hooks, cron) don't get
    them from pam_systemd, causing 'Failed to connect to bus' errors."""
    env = os.environ.copy()
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS",
                   f"unix:path={env['XDG_RUNTIME_DIR']}/bus")
    return env


def detect_backend() -> str:
    """Return 'systemd', 'launchd', or 'manual'."""
    if sys.platform == "darwin":
        if shutil.which("launchctl") and os.path.exists(LAUNCHD_PLIST):
            return "launchd"
        return "manual"
    if shutil.which("systemctl"):
        # User bus must actually be reachable — it dies if user@.service
        # was OOM-killed (observed in production).
        r = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            env=_systemd_env(), capture_output=True, text=True, timeout=10)
        # 'running'/'degraded' both mean the bus is up
        if r.returncode == 0 or r.stdout.strip() in ("running", "degraded"):
            return "systemd"
    return "manual"


def _lock_pid() -> int | None:
    """Read the listener PID from the lock file, or None."""
    try:
        with open(LOCK_FILE) as f:
            return int(f.read().strip() or 0) or None
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _cleanup_stale_lock() -> bool:
    """Remove the lock file if its PID is dead. Returns True if removed."""
    pid = _lock_pid()
    if pid is not None and not _pid_alive(pid):
        try:
            os.remove(LOCK_FILE)
            return True
        except OSError:
            pass
    return False


# --- actions ---


def status() -> int:
    """Print listener status. Returns exit code (0 = running)."""
    backend = detect_backend()
    pid = _lock_pid()
    alive = pid is not None and _pid_alive(pid)
    print(f"Backend: {backend}")
    if alive:
        print(f"Listener: running (pid {pid})")
    elif pid is not None:
        print(f"Listener: NOT running (stale lock, pid {pid} dead)")
    else:
        print("Listener: not running")

    sys.stdout.flush()  # keep header above unbuffered subprocess output
    if backend == "systemd":
        subprocess.run(["systemctl", "--user", "--no-pager", "status",
                        "astra"], env=_systemd_env())
    elif backend == "launchd":
        subprocess.run(["launchctl", "print",
                        f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
                       capture_output=False)
    return 0 if alive else 1


def start() -> int:
    backend = detect_backend()
    if _cleanup_stale_lock():
        print("Removed stale lock file.")
    pid = _lock_pid()
    if pid is not None and _pid_alive(pid):
        print(f"Listener already running (pid {pid}).")
        return 0

    if backend == "systemd":
        r = subprocess.run(["systemctl", "--user", "start", "astra"],
                           env=_systemd_env())
        print("Started via systemd." if r.returncode == 0 else "systemd start failed.")
        return r.returncode
    if backend == "launchd":
        r = subprocess.run(
            ["launchctl", "kickstart", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"])
        print("Started via launchd." if r.returncode == 0 else "launchd start failed.")
        return r.returncode

    # Manual: spawn detached
    wrapper = shutil.which("astra") or os.path.expanduser("~/bin/astra")
    log = MAC_LOG_FILE if sys.platform == "darwin" else "/tmp/astra_listener.log"
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "a") as logf:
        subprocess.Popen(
            [wrapper, "listen"],
            stdout=logf, stderr=logf,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    time.sleep(2)
    pid = _lock_pid()
    if pid is not None and _pid_alive(pid):
        print(f"Started manually (pid {pid}, log: {log}).")
        return 0
    print(f"Manual start may have failed — check {log}", file=sys.stderr)
    return 1


def stop() -> int:
    backend = detect_backend()
    if backend == "systemd":
        r = subprocess.run(["systemctl", "--user", "stop", "astra"],
                           env=_systemd_env())
        print("Stopped via systemd." if r.returncode == 0 else "systemd stop failed.")
        return r.returncode
    if backend == "launchd":
        r = subprocess.run(
            ["launchctl", "kill", "SIGTERM",
             f"gui/{os.getuid()}/{LAUNCHD_LABEL}"])
        print("Stopped via launchd." if r.returncode == 0 else "launchd stop failed.")
        return r.returncode

    pid = _lock_pid()
    if pid is None or not _pid_alive(pid):
        print("Listener not running.")
        _cleanup_stale_lock()
        return 0
    os.kill(pid, _signal.SIGTERM)
    for _ in range(10):
        if not _pid_alive(pid):
            print(f"Stopped (pid {pid}).")
            return 0
        time.sleep(0.5)
    os.kill(pid, _signal.SIGKILL)
    print(f"Killed (pid {pid}) after SIGTERM timeout.")
    return 0


def restart() -> int:
    """Stop + cleanups + start."""
    backend = detect_backend()
    if backend == "systemd":
        _cleanup_stale_lock()
        r = subprocess.run(["systemctl", "--user", "restart", "astra"],
                           env=_systemd_env())
        if r.returncode == 0:
            print("Restarted via systemd.")
            return 0
        print("systemd restart failed — falling back to manual.", file=sys.stderr)
    elif backend == "launchd":
        _cleanup_stale_lock()
        r = subprocess.run(
            ["launchctl", "kickstart", "-k",
             f"gui/{os.getuid()}/{LAUNCHD_LABEL}"])
        if r.returncode == 0:
            print("Restarted via launchd.")
            return 0
        print("launchd restart failed — falling back to manual.", file=sys.stderr)

    rc = stop()
    if rc != 0:
        return rc
    _cleanup_stale_lock()
    return start()


def read_logs(n: int = 30) -> str | None:
    """Return the last n log lines as a string, or None if unavailable.

    Tries journalctl (Linux/systemd) first, then file-based logs
    (macOS launchd plist redirects stdout there; manual mode logs to
    /tmp/astra_listener.log).
    """
    if shutil.which("journalctl"):
        try:
            r = subprocess.run(
                ["journalctl", "--user", "-u", "astra", "-n", str(n),
                 "--no-pager"],
                env=_systemd_env(), capture_output=True, text=True,
                timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
        except Exception:
            pass
    for path in (MAC_LOG_FILE, "/tmp/astra_listener.log"):
        if os.path.exists(path):
            try:
                with open(path) as f:
                    lines = f.readlines()
                return "".join(lines[-n:])
            except OSError:
                continue
    return None


def logs(n: int = 30) -> int:
    """Print the last n log lines. Returns exit code."""
    out = read_logs(n)
    if out is None:
        print("No logs found (journalctl unavailable, no log file).",
              file=sys.stderr)
        return 1
    print(out, end="")
    return 0


def generate_launchd_plist(repo_root: str) -> str:
    """Return launchd plist XML for the astra listener.

    `repo_root` is the dotfiles checkout path (containing scripts/astra).
    """
    pixi = shutil.which("pixi") or os.path.expanduser("~/.pixi/bin/pixi")
    manifest = os.path.join(repo_root, "scripts/astra/pixi.toml")

    # launchd starts agents with a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin)
    # that excludes Homebrew, so bare `tmux`/`pixi` invocations fail with
    # "command not found" and the daemon detects zero sessions.  Build a PATH
    # that includes the dirs holding the tools we actually shell out to.
    path_dirs: list[str] = []
    for tool in ("tmux", "pixi"):
        found = shutil.which(tool)
        if found:
            path_dirs.append(os.path.dirname(found))
    path_dirs += ["/opt/homebrew/bin", "/usr/local/bin",
                  "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    seen: set[str] = set()
    daemon_path = ":".join(d for d in path_dirs if d and not (d in seen or seen.add(d)))

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{pixi}</string>
        <string>run</string>
        <string>-m</string>
        <string>{manifest}</string>
        <string>astra</string>
        <string>listen</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>{MAC_LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>{MAC_LOG_FILE}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <key>PATH</key>
        <string>{daemon_path}</string>
    </dict>
</dict>
</plist>
"""
