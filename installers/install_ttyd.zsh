#!/bin/zsh

# --- Configuration ---
DEFAULT_PORT=2719
YES=false
[[ "$1" == "-y" ]] && YES=true

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
ExecStart=/usr/local/bin/ttyd -t allowProposedApi=true -t macOptionIsMeta=true -p $TTYD_PORT -W tmux new-session -A -s main
Restart=always
RestartSec=5
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

[Install]
WantedBy=multi-user.target
EOF

# 5. Launch and Verify
sudo systemctl daemon-reload
sudo systemctl enable ttyd
sudo systemctl restart ttyd

echo "------------------------------------------------"
echo "✅ Setup Complete!"
echo "URL: http://$(curl -s ifconfig.me):$TTYD_PORT"
echo "------------------------------------------------"
echo "⚠️  Reminder: Update your EC2 Security Group to allow TCP port $TTYD_PORT."
