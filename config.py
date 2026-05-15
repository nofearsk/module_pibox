"""
PiBox Configuration Management
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env_file():
    """
    Load KEY=VALUE pairs from a .env file next to this module.

    Lookup order:
    1. $PIBOX_ENV_FILE if set
    2. <module>/.env

    Existing process env vars take precedence over the file, so systemd
    `Environment=` directives still win.
    """
    path = os.environ.get('PIBOX_ENV_FILE') or os.path.join(BASE_DIR, '.env')
    if not os.path.isfile(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


_load_env_file()

# Base paths
DATA_DIR = os.environ.get('PIBOX_DATA_DIR', '/var/pibox')
DB_PATH = os.path.join(DATA_DIR, 'pibox.db')
IMAGES_DIR = os.path.join(DATA_DIR, 'images')

# Server ports
HTTP_PORT = int(os.environ.get('PIBOX_HTTP_PORT', 8080))
WS_PORT = int(os.environ.get('PIBOX_WS_PORT', 8081))

# GPIO Configuration - BCM pin numbers for 8 relay channels
RELAY_PINS = {
    1: 5,
    2: 6,
    3: 13,
    4: 16,
    5: 19,
    6: 20,
    7: 21,
    8: 26,
}

# Default configuration values
DEFAULTS = {
    # Odoo Server Connection (JSON-RPC)
    'odoo_url': '',              # Base URL (e.g., https://myodoo.com)
    'odoo_db': '',               # Database name
    'odoo_username': '',         # Odoo login username
    'odoo_uid': '',              # User ID from session
    'odoo_session_id': '',       # Session cookie

    # Site Configuration
    'site_id': '',               # Site ID in Odoo

    # Sync Settings
    'sync_interval': '300',      # Sync interval in seconds (5 minutes)

    # Barrier Control
    'barrier_pulse_duration': '1.0',  # Pulse duration in seconds
    'image_retention_days': '7',      # Days to keep local images
    'disk_threshold_percent': '85',   # Auto-delete oldest images when disk exceeds this %

    # Relay Mode: 'gpio' or 'web' (Iotzone V5+ Ethernet Relay)
    'relay_mode': 'gpio',             # Default to GPIO relay

    # Web Relay Settings (Iotzone V5+ 8-Channel Ethernet Relay)
    'web_relay_enabled': 'false',     # Enable web relay
    'web_relay_ip': '',               # IP address of relay board
    'web_relay_port': '80',           # HTTP port
    'web_relay_username': 'admin',    # Web auth username
    'web_relay_password': '12345678', # Web auth password
    'web_relay_pulse_time': '1.0',    # Pulse time in seconds (configured on board)

    # Local ANPR (fast-plate-ocr + open-image-models, CPU-only)
    'lpr_enabled': 'false',                                       # Master switch
    'lpr_detector_model': 'yolo-v9-t-384-license-plate-end2end',  # open-image-models hub id
    'lpr_ocr_model': 'cct-xs-v1-global-model',                    # fast-plate-ocr hub id
}


class Config:
    """Configuration manager using SQLite backend"""

    _instance = None
    _cache = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._cache:
            self._load_from_db()

    def _load_from_db(self):
        """Load config from database"""
        try:
            from database.db import get_db
            db = get_db()
            cursor = db.execute('SELECT key, value FROM config')
            for row in cursor.fetchall():
                self._cache[row['key']] = row['value']
        except Exception:
            # Database might not exist yet
            pass

        # Apply defaults for missing keys
        for key, value in DEFAULTS.items():
            if key not in self._cache:
                self._cache[key] = value

    def get(self, key, default=None):
        """Get config value"""
        return self._cache.get(key, default)

    def set(self, key, value):
        """Set config value"""
        from database.db import get_db
        db = get_db()
        db.execute(
            'INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)',
            (key, str(value))
        )
        db.commit()
        self._cache[key] = str(value)

    def get_all(self):
        """Get all config as dict"""
        return dict(self._cache)

    def set_bulk(self, data):
        """Set multiple config values"""
        from database.db import get_db
        db = get_db()
        for key, value in data.items():
            db.execute(
                'INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)',
                (key, str(value))
            )
            self._cache[key] = str(value)
        db.commit()

    def clear_cache(self):
        """Clear cache and reload from database"""
        self._cache.clear()
        self._load_from_db()

    # Properties for common config values
    @property
    def odoo_url(self):
        return self.get('odoo_url', '')

    @property
    def odoo_db(self):
        return self.get('odoo_db', '')

    @property
    def odoo_username(self):
        return self.get('odoo_username', '')

    @property
    def odoo_uid(self):
        return self.get('odoo_uid', '')

    @property
    def site_id(self):
        return self.get('site_id', '')

    @property
    def sync_interval(self):
        return int(self.get('sync_interval', 300))

    @property
    def barrier_pulse_duration(self):
        return float(self.get('barrier_pulse_duration', 1.0))

    @property
    def is_configured(self):
        """Check if Odoo is configured and authenticated"""
        return bool(self.odoo_url and self.odoo_uid)


# Singleton instance
config = Config()
