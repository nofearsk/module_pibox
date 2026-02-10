#!/bin/bash
# PiBox Edge Controller Setup Script
# For Raspberry Pi 4 with Debian 13 Trixie (64-bit)

set -e

echo "=================================="
echo "  PiBox Edge Controller Setup"
echo "=================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (sudo ./setup.sh)"
    exit 1
fi

# Update package list
echo "[1/6] Updating package list..."
apt update

# Install system dependencies
echo "[2/6] Installing system dependencies..."
apt install -y python3-pip python3-lgpio python3-flask

# Install Python packages
echo "[3/6] Installing Python packages..."
pip3 install websockets boto3 requests --break-system-packages

# Create data directories
echo "[4/6] Creating data directories..."
mkdir -p /var/pibox/images
chown -R $SUDO_USER:$SUDO_USER /var/pibox

# Make app executable
echo "[5/6] Setting permissions..."
chmod +x app.py

# Install systemd service
echo "[6/8] Installing systemd service..."
cp pibox.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable pibox

# Setup Git for auto-updates
echo "[7/8] Setting up Git for auto-updates..."
PIBOX_DIR=$(pwd)
if [ ! -d ".git" ]; then
    git init
    git remote add origin https://github.com/nofearsk/module_pibox.git
    git fetch origin
    git reset --hard origin/master
fi

# Create update script
echo "[8/8] Creating update script and cron job..."
cat > update.sh << 'EOF'
#!/bin/bash
echo "=== PiBox Update ==="
cd /home/admin/pibox

echo "Pulling latest from GitHub..."
git fetch origin
git reset --hard origin/master

echo "Restarting service..."
sudo systemctl restart pibox

echo "Done! Checking status..."
sudo systemctl status pibox --no-pager | head -5
EOF
chmod +x update.sh

# Setup daily auto-update at 3 AM
(crontab -u $SUDO_USER -l 2>/dev/null | grep -v 'pibox/update.sh'; echo "0 3 * * * $PIBOX_DIR/update.sh >> $PIBOX_DIR/update.log 2>&1") | crontab -u $SUDO_USER -

echo ""
echo "=================================="
echo "  Setup Complete!"
echo "=================================="
echo ""
echo "To start the service:"
echo "  sudo systemctl start pibox"
echo ""
echo "To view logs:"
echo "  sudo journalctl -u pibox -f"
echo ""
echo "Access the web interface at:"
echo "  http://$(hostname -I | cut -d' ' -f1):8080"
echo ""
echo "WebSocket server at:"
echo "  ws://$(hostname -I | cut -d' ' -f1):8081"
echo ""
echo "Configure settings at:"
echo "  http://$(hostname -I | cut -d' ' -f1):8080/settings"
echo ""
echo "Auto-update enabled:"
echo "  Daily at 3:00 AM from GitHub"
echo "  Manual update: ./update.sh"
echo "  Update log: update.log"
echo ""
