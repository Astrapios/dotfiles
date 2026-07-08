#!/bin/sh
# 1-minute cron safety net for the astra systemd --user service.
#
# systemd (with lingering enabled) already auto-restarts astra on crash/OOM via
# the unit's Restart=on-failure. This is belt-and-suspenders for the one case
# systemd won't recover on its own: after StartLimitBurst is exceeded the unit
# lands in "failed" and stays there until reset-failed. This script clears that
# and restarts. It is idempotent — a no-op while the service is active.
#
# Install: * * * * * /home/ubuntu/.dotfiles/scripts/astra/astra-watchdog.sh
export XDG_RUNTIME_DIR="/run/user/$(id -u)"

if ! systemctl --user is-active --quiet astra.service; then
    systemctl --user reset-failed astra.service 2>/dev/null
    systemctl --user start astra.service
    echo "$(date '+%F %T') astra was down -> started via systemctl (exit $?)" \
        >> /tmp/astra_watchdog.log
fi
