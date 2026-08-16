#!/bin/zsh

# --- Configuration ---
DEFAULT_PORT=2719
YES=false
[[ "$1" == "-y" ]] && YES=true
DOTFILES_ROOT=${0:a:h:h}

# 1. Detect Architecture and Latest Version
ARCH=$(uname -m)
[[ "$ARCH" == "x86_64" ]] && TTYD_ARCH="x86_64"
[[ "$ARCH" == "aarch64" ]] && TTYD_ARCH="aarch64"

LATEST_VERSION=$(curl -s https://api.github.com/repos/tsl0922/ttyd/releases/latest | grep -Po '"tag_name": "\K.*?(?=")')

if [[ -z "$LATEST_VERSION" ]]; then
    echo "❌ Error: Could not fetch ttyd version."
    exit 1
fi

# 2. Collect Configuration
if $YES; then
    TTYD_PORT=$DEFAULT_PORT
else
    echo "--- ttyd Configuration ---"
    echo -n "Enter Port [$DEFAULT_PORT]: "
    read TTYD_PORT
    TTYD_PORT=${TTYD_PORT:-$DEFAULT_PORT}
fi

# 3. Install Dependencies & ttyd
echo "--- Installing Dependencies & ttyd $LATEST_VERSION ---"
sudo apt update && sudo apt install -y tmux wget curl
DOWNLOAD_URL="https://github.com/tsl0922/ttyd/releases/download/${LATEST_VERSION}/ttyd.${TTYD_ARCH}"
sudo wget -qO /usr/local/bin/ttyd "$DOWNLOAD_URL"
sudo chmod +x /usr/local/bin/ttyd

# 4. Create Systemd Service
CURRENT_USER=$(whoami)
SERVICE_FILE="/etc/systemd/system/ttyd.service"

echo "--- Generating Systemd Service at $SERVICE_FILE ---"
sudo bash -c "cat > $SERVICE_FILE" <<EOF
[Unit]
Description=ttyd - Terminal over Web
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
# Configuration applied here:
ExecStart=/usr/local/bin/ttyd \
    -t allowProposedApi=true \
    -t macOptionIsMeta=true \
    -t fontSize=14 \
    -t "fontFamily='Source Code Pro, Menlo, Consolas, monospace'" \
    -t "theme={\"background\":\"#000000\",\"foreground\":\"#dddddd\",\"cursor\":\"#bbbbbb\",\"black\":\"#000000\",\"red\":\"#cc0403\",\"green\":\"#19cb00\",\"yellow\":\"#cecb00\",\"blue\":\"#0d73cc\",\"magenta\":\"#cb1ed1\",\"cyan\":\"#0dcdcd\",\"white\":\"#dddddd\",\"brightBlack\":\"#767676\",\"brightRed\":\"#f2201f\",\"brightGreen\":\"#23fd00\",\"brightYellow\":\"#fffd00\",\"brightBlue\":\"#1a8fff\",\"brightMagenta\":\"#fd28ff\",\"brightCyan\":\"#14ffff\",\"brightWhite\":\"#ffffff\"}" \
    -p $TTYD_PORT -W $DOTFILES_ROOT/scripts/ttyd_tmux_attach.sh
Restart=always
RestartSec=5
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

[Install]
WantedBy=multi-user.target
EOF

# 5. tmux server as a systemd user service (decoupled from ttyd)
# The server must not be forked by ttyd's client — it would land in
# ttyd.service's cgroup and die on every ttyd restart or OOM event.
mkdir -p ~/.config/systemd/user
cp $DOTFILES_ROOT/scripts/tmux-main.service ~/.config/systemd/user/tmux-main.service
systemctl --user daemon-reload
systemctl --user enable tmux-main.service
tmux has-session -t main 2>/dev/null || systemctl --user start tmux-main.service
sudo loginctl enable-linger $CURRENT_USER 2>/dev/null || true

# Protect the user manager from the OOM killer (default adj=100 makes it a
# preferred victim over the runaway compute causing the pressure; its death
# tears down every tmux pane) and restart it if it dies anyway.
sudo mkdir -p /etc/systemd/system/user@$(id -u $CURRENT_USER).service.d
sudo tee /etc/systemd/system/user@$(id -u $CURRENT_USER).service.d/oom-protect.conf > /dev/null <<'OOMEOF'
[Service]
OOMScoreAdjust=-400
Restart=on-failure
RestartSec=5
OOMEOF
sudo systemctl daemon-reload

# 6. Launch and Verify
sudo systemctl daemon-reload
sudo systemctl enable ttyd
sudo systemctl restart ttyd

echo "------------------------------------------------"
echo "✅ Setup Complete!"
echo "URL: http://$(curl -s ifconfig.me):$TTYD_PORT"
echo "------------------------------------------------"
echo "⚠️  Reminder: Update your EC2 Security Group to allow TCP port $TTYD_PORT."
