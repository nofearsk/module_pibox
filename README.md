# PiBox Edge Controller

Vehicle Access Control System for Raspberry Pi with ANPR integration.

## Features

1. **Relay Control** - 8-channel relay board control via GPIO
2. **Odoo Sync** - Sync vehicle data from Odoo server using REST API
3. **ANPR Camera Controller** - Receive plate data from Hikvision/Dahua cameras
4. **WebSocket Broadcast** - Real-time updates to connected clients

## Quick Install

```bash
# 1. Copy files to your Pi
scp -r module_pibox admin@<pi-ip>:/home/admin/pibox

# 2. SSH into Pi
ssh admin@<pi-ip>

# 3. Install dependencies
cd /home/admin/pibox
pip3 install -r requirements.txt

# 4. Create data directory
sudo mkdir -p /var/pibox
sudo chown $USER:$USER /var/pibox

# 5. Start the server
sudo python3 app.py
```

## First Time Setup

1. Open browser: `http://<raspberry-pi-ip>:8080`
2. You'll be redirected to the login page
3. Enter your Odoo server URL (e.g., `https://your-odoo.com`)
4. Enter your Odoo username and password
5. Click "Connect to Odoo"

The system will authenticate and start syncing vehicle data automatically.

## Architecture

```
+------------------+     +------------------+     +------------------+
|   ANPR Camera    |---->|  PiBox (Pi)      |---->|   Odoo Server    |
|  (Hikvision/     |     |                  |     |   (REST API)     |
|   Dahua)         |     |  - Flask HTTP    |     |                  |
+------------------+     |  - WebSocket     |     +------------------+
                         |  - SQLite DB     |
+------------------+     |  - GPIO Control  |
|   Web Browser    |<--->|                  |
|   (Dashboard)    |     +--------+---------+
+------------------+              |
                                  v
                         +------------------+
                         |  8-Channel Relay |
                         |  (Barrier/Door)  |
                         +------------------+
```

## API Endpoints

### Authentication
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/login` | POST | Login to Odoo |
| `/api/auth/logout` | POST | Logout |
| `/api/auth/status` | GET | Get auth status |

### Relay Control
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Get all relay states |
| `/api/relay/<1-8>/on` | POST | Turn relay ON |
| `/api/relay/<1-8>/off` | POST | Turn relay OFF |
| `/api/relay/<1-8>/pulse` | POST | Pulse for 1 second |
| `/api/all/on` | POST | All relays ON |
| `/api/all/off` | POST | All relays OFF |

### ANPR Camera Events
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/anpr/hikvision` | POST | Receive Hikvision events |
| `/api/anpr/dahua` | POST | Receive Dahua events |
| `/api/anpr/generic` | POST | Generic ANPR events |
| `/api/anpr/test` | GET/POST | Test endpoint |

### Vehicles & Access
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/vehicles` | GET | List all vehicles |
| `/api/vehicles/search` | GET | Search by plate |
| `/api/vehicles/<plate>` | GET | Get vehicle details |
| `/api/access-logs` | GET | Recent access logs |
| `/api/access-logs/stats` | GET | Today's statistics |

### Barriers
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/barriers` | GET | List barrier mappings |
| `/api/barriers` | POST | Create mapping |
| `/api/barriers/<id>` | PUT | Update mapping |
| `/api/barriers/<id>` | DELETE | Delete mapping |

### Sync
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sync/status` | GET | Get sync status |
| `/api/sync/now` | POST | Force immediate sync |
| `/api/sync/test` | GET | Test Odoo connection |

## WebSocket Events

Connect to `ws://<pi-ip>:8081` for real-time updates:

```javascript
const ws = new WebSocket('ws://192.168.1.100:8081');

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);

    switch(data.type) {
        case 'access_event':
            // Vehicle detected
            console.log('Plate:', data.data.plate);
            console.log('Access:', data.data.access_granted);
            break;

        case 'barrier_status':
            // Relay states changed
            console.log('Relays:', data.data.relays);
            break;

        case 'stats':
            // Daily statistics update
            console.log('Stats:', data.data);
            break;
    }
};
```

## Run as Service

```bash
# Copy service file
sudo cp pibox.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable pibox
sudo systemctl start pibox

# Check status
sudo systemctl status pibox
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

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PIBOX_HTTP_PORT` | 8080 | HTTP server port |
| `PIBOX_WS_PORT` | 8081 | WebSocket server port |
| `PIBOX_DATA_DIR` | /var/pibox | Data directory |
| `PIBOX_SECRET_KEY` | pibox-secret-key | Flask secret key |

## Camera Setup

### Hikvision
Configure the camera to send HTTP notifications to:
```
http://<pi-ip>:8080/api/anpr/hikvision
```

### Dahua
Configure the camera to send HTTP notifications to:
```
http://<pi-ip>:8080/api/anpr/dahua
```

## Troubleshooting

**"Could not open GPIO chip"**
- Run with sudo: `sudo python3 app.py`
- Check chip: `ls /dev/gpiochip*`

**"Address already in use"**
- Kill existing process: `sudo pkill -f app.py`
- Or change port via environment variable

**"Not authenticated"**
- Login via web UI at `http://<pi-ip>:8080/login`
- Or call `/api/auth/login` endpoint

**Relays not clicking**
- Check yellow jumpers are connected on the relay board
- Verify 5V power supply is adequate
