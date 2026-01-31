# RPi Relay Board (B) Web Controller

Web-based control panel for the 8-channel RPi Relay Board on Debian 13 Trixie.

## Features

- **ON/OFF Control** - Turn individual relays on or off
- **1-Second Pulse** - Momentary activation (perfect for door strikes/barriers)
- **All ON/OFF** - Master control for all 8 relays
- **Real-time Status** - Visual feedback for relay states
- **Dark Theme** - CCC tactical-style interface
- **Mobile Friendly** - Responsive design

## Quick Install

```bash
# 1. Copy files to your Pi
scp -r relay_web admin@<pi-ip>:/home/admin/

# 2. SSH into Pi
ssh admin@<pi-ip>

# 3. Run setup
cd relay_web
chmod +x setup.sh
./setup.sh

# 4. Start the server
sudo python3 app.py
```

## Access the Controller

Open in browser: `http://<raspberry-pi-ip>:8080`

## Run as Service (Auto-start on Boot)

```bash
# Copy service file
sudo cp relay-controller.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable relay-controller
sudo systemctl start relay-controller

# Check status
sudo systemctl status relay-controller
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Get all relay states |
| `/api/relay/<1-8>/on` | POST | Turn relay ON |
| `/api/relay/<1-8>/off` | POST | Turn relay OFF |
| `/api/relay/<1-8>/pulse` | POST | Pulse for 1 second |
| `/api/all/on` | POST | All relays ON |
| `/api/all/off` | POST | All relays OFF |

## MyPico Integration Example

```python
import requests

RELAY_API = "http://192.168.1.100:8080"

def unlock_door(relay_channel=1):
    """Pulse relay to unlock door for 1 second"""
    response = requests.post(f"{RELAY_API}/api/relay/{relay_channel}/pulse")
    return response.json()

def set_alarm(state=True):
    """Control alarm relay"""
    action = "on" if state else "off"
    response = requests.post(f"{RELAY_API}/api/relay/8/{action}")
    return response.json()
```

## GPIO Pin Mapping

| Relay | BCM Pin | Board Pin |
|-------|---------|-----------|
| 1 | 5 | 29 |
| 2 | 6 | 31 |
| 3 | 13 | 33 |
| 4 | 16 | 36 |
| 5 | 19 | 35 |
| 6 | 20 | 38 |
| 7 | 21 | 40 |
| 8 | 26 | 37 |

## Troubleshooting

**"Could not open GPIO chip"**
- Run with sudo: `sudo python3 app.py`
- Check chip: `ls /dev/gpiochip*`

**"Address already in use"**
- Kill existing process: `sudo pkill -f app.py`
- Or use different port: Change `port=8080` in app.py

**Relays not clicking**
- Check yellow jumpers are connected on the board
- Verify 5V power supply is adequate
