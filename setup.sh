#!/bin/bash
# RPi Relay Board Web Controller - Setup Script
# For Debian 13 Trixie / Raspberry Pi 4 64-bit

echo "=========================================="
echo "  RPi Relay Web Controller Setup"
echo "=========================================="

# Update packages
echo "[1/3] Updating packages..."
sudo apt update

# Install dependencies
echo "[2/3] Installing Python dependencies..."
sudo apt install -y python3-pip python3-lgpio python3-flask

# If flask not available via apt, use pip
if ! python3 -c "import flask" 2>/dev/null; then
    echo "Installing Flask via pip..."
    pip3 install flask --break-system-packages
fi

# Make app executable
chmod +x app.py

echo "[3/3] Setup complete!"
echo ""
echo "=========================================="
echo "  To run the server:"
echo "  sudo python3 app.py"
echo ""
echo "  Then open in browser:"
echo "  http://<raspberry-pi-ip>:8080"
echo "=========================================="
