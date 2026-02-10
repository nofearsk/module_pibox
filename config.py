"""
PiBox Configuration Management
"""
import os

# Base paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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
