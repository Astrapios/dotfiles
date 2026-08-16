#!/bin/sh
# ttyd start command: attach the web terminal to the main tmux session.
#
# The tmux server must NOT be forked from here — a server forked by ttyd's
# client lands in ttyd.service's cgroup, so every ttyd stop (nightly
# unattended-upgrades restart, OOM kill) SIGTERMs the server and all panes
# with it. The server is owned by the tmux-main systemd user service
# instead; this wrapper only ensures it is running, then attaches.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
tmux has-session -t main 2>/dev/null || systemctl --user start tmux-main.service
exec tmux new-session -A -s main
