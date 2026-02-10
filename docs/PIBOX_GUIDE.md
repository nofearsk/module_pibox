# PiBox Edge Controller

## Overview

**PiBox** is an edge computing solution for autonomous vehicle access control. It runs on a Raspberry Pi, receives ANPR camera events, makes local access decisions, controls barrier relays, and syncs with Odoo.

## Features

- **ANPR Integration**: Receive events from Hikvision cameras
- **Local Vehicle Database**: Synced from Odoo's `units.vehicles`
- **Automatic Barrier Control**: Open barriers for registered vehicles
- **Real-time WebSocket**: Live updates to web UI and tablets
- **Offline Resilience**: Continues working if Odoo is unreachable
- **S3 Image Upload**: Store ANPR images in cloud storage

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    RASPBERRY PI (PiBox)                      │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ HTTP Server  │  │ WebSocket    │  │ Sync Service │       │
│  │ Port 8080    │  │ Port 8081    │  │ (to Odoo)    │       │
│  └──────────────┘  └──────────────┘  └──────────────┘       │
│         │                │                                   │
│         ▼                ▼                                   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              SQLite Local Database                    │   │
│  │  • vehicles (synced from Odoo)                       │   │
│  │  • barrier_mapping (camera → relay config)           │   │
│  │  • access_logs                                       │   │
│  └──────────────────────────────────────────────────────┘   │
│                            │                                 │
│                            ▼                                 │
│                   ┌──────────────┐                           │
│                   │ GPIO Relays  │                           │
│                   │ (8 channels) │                           │
│                   └──────────────┘                           │
└─────────────────────────────────────────────────────────────┘
```

## Installation

### Prerequisites
- Raspberry Pi 4 with Debian 13 Trixie (64-bit)
- Network connectivity
- 8-channel relay board connected via GPIO

### Quick Install

```bash
# 1. Copy files to Pi
scp -r module_pibox admin@<pi-ip>:/home/admin/pibox

# 2. SSH into Pi
ssh admin@<pi-ip>

# 3. Run setup
cd /home/admin/pibox
sudo ./setup.sh

# 4. Start service
sudo systemctl start pibox
```

### Manual Installation

```bash
# Install dependencies
sudo apt update
sudo apt install -y python3-pip python3-lgpio python3-flask
pip3 install websockets boto3 requests --break-system-packages

# Create data directory
sudo mkdir -p /var/pibox/images
sudo chown -R $USER:$USER /var/pibox

# Run directly
sudo python3 app.py
```

## Configuration

After installation, configure via web UI at `http://<pi-ip>:8080/settings`

### Required Settings

| Setting | Description |
|---------|-------------|
| Odoo URL | Full URL to Odoo server (e.g., `https://your-odoo.com`) |
| Device Code | `reg_code` from `location.devices` |
| Device Password | `reg_password` from `location.devices` |
| Site ID | Site ID this Pi serves |

### Optional Settings

| Setting | Description | Default |
|---------|-------------|---------|
| Sync Interval | How often to sync vehicles (seconds) | 300 |
| Barrier Pulse Duration | How long to open barrier (seconds) | 1.0 |
| S3 Bucket | AWS S3 bucket for images | (disabled) |

## Barrier Mapping

Configure which camera IPs trigger which relay channels:

1. Go to `http://<pi-ip>:8080/barriers`
2. Click "Add Mapping"
3. Enter:
   - **Camera IP**: IP address of ANPR camera
   - **Camera Name**: Friendly name (e.g., "Main Gate Entry")
   - **Relay Channels**: Comma-separated (e.g., `1,2` for two barriers)
   - **Direction**: entry, exit, or both

Example mappings:
- Camera `192.168.1.100` → Relays `[1]` (Main Gate)
- Camera `192.168.1.101` → Relays `[1,2]` (Opens both barriers)

## API Endpoints

### ANPR Receiver

| Endpoint | Description |
|----------|-------------|
| `POST /api/anpr/hikvision` | Receive Hikvision ANPR events |
| `POST /api/anpr/generic` | Generic plate notification |

### Vehicles

| Endpoint | Description |
|----------|-------------|
| `GET /api/vehicles` | List all vehicles |
| `GET /api/vehicles/search?plate=ABC` | Search by plate |

### Access Logs

| Endpoint | Description |
|----------|-------------|
| `GET /api/access-logs` | Recent access events |
| `GET /api/access-logs/stats` | Today's statistics |

### Relay Control

| Endpoint | Description |
|----------|-------------|
| `GET /api/relay/status` | Get all relay states |
| `POST /api/relay/<1-8>/on` | Turn relay ON |
| `POST /api/relay/<1-8>/off` | Turn relay OFF |
| `POST /api/relay/<1-8>/pulse` | Pulse relay |

### System

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Health check |
| `POST /api/sync/now` | Force sync |
| `GET /api/sync/status` | Sync status |

## WebSocket Events

Connect to `ws://<pi-ip>:8081` for real-time updates.

### Events (Server → Client)

**access_event** - When vehicle is detected
```json
{
    "type": "access_event",
    "data": {
        "plate": "ABC1234",
        "access_granted": true,
        "vehicle_type": "resident",
        "owner_name": "John Doe",
        "unit_name": "A-12-03",
        "image_url": "/images/20240115/abc1234.jpg"
    }
}
```

**stats** - Periodic statistics (every 30s)
```json
{
    "type": "stats",
    "data": {
        "today_total": 156,
        "today_granted": 142,
        "today_denied": 14
    }
}
```

## Odoo Integration

### Device Registration

1. In Odoo, create `location.devices` record
2. Note the auto-generated `reg_code` and `reg_password`
3. Enter these in PiBox settings

### API Endpoints (Odoo Side)

| Endpoint | Description |
|----------|-------------|
| `GET /api/pibox/vehicles` | Get vehicles for site |
| `POST /api/pibox/access-log` | Create access log |
| `GET /api/pibox/ping` | Test connection |

## File Structure

```
module_pibox/
├── app.py                 # Main entry point
├── config.py              # Configuration
├── requirements.txt       # Python dependencies
├── setup.sh               # Installation script
├── pibox.service          # Systemd service
├── database/
│   ├── db.py              # SQLite connection
│   └── models.py          # Data access
├── services/
│   ├── relay_service.py   # GPIO control
│   ├── sync_service.py    # Odoo sync
│   ├── anpr_service.py    # ANPR parsing
│   ├── access_service.py  # Decision logic
│   ├── websocket_service.py # Real-time
│   └── s3_service.py      # Image upload
├── routes/
│   ├── anpr_routes.py     # ANPR endpoints
│   ├── api_routes.py      # REST API
│   └── web_routes.py      # Web pages
├── templates/             # HTML templates
└── static/                # CSS, JS
```

## Troubleshooting

### GPIO Error
```bash
# Must run as root for GPIO access
sudo python3 app.py

# Or check GPIO chip
ls /dev/gpiochip*
```

### Connection to Odoo Failed
1. Check Odoo URL in settings
2. Verify `reg_code` and `reg_password`
3. Test: `curl "https://your-odoo.com/api/pibox/ping?code=ABC12&password=PASS1234"`

### Relays Not Clicking
1. Check yellow jumpers on relay board
2. Verify 5V power supply (2A recommended)
3. Test manually: `curl http://localhost:8080/api/relay/1/pulse`

### WebSocket Not Connecting
1. Check port 8081 is not blocked
2. Verify WebSocket server started in logs
3. Test: `wscat -c ws://localhost:8081`

## Service Management

```bash
# Start/Stop/Restart
sudo systemctl start pibox
sudo systemctl stop pibox
sudo systemctl restart pibox

# Check status
sudo systemctl status pibox

# View logs
sudo journalctl -u pibox -f

# View recent logs
sudo journalctl -u pibox --since "1 hour ago"
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

**Control Logic:** Active-LOW (GPIO LOW = Relay ON)
