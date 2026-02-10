"""
SQLite Database Connection Manager
"""
import sqlite3
import os
from contextlib import contextmanager

# Will be set from config
DB_PATH = None
_connection = None


def get_db_path():
    global DB_PATH
    if DB_PATH is None:
        from config import DB_PATH as cfg_path
        DB_PATH = cfg_path
    return DB_PATH


def get_db():
    """Get database connection (creates if needed)"""
    global _connection
    if _connection is None:
        db_path = get_db_path()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        _connection = sqlite3.connect(db_path, check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        init_db(_connection)
    return _connection


def close_db():
    """Close database connection"""
    global _connection
    if _connection:
        _connection.close()
        _connection = None


def init_db(conn=None):
    """Initialize database schema"""
    if conn is None:
        conn = get_db()

    cursor = conn.cursor()

    # Config table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

    # Vehicles table (synced from Odoo units.vehicles)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            odoo_id INTEGER UNIQUE,
            plate TEXT,
            iu_number TEXT,
            unit_id INTEGER,
            unit_name TEXT,
            owner_name TEXT,
            valid_from TEXT,
            valid_to TEXT,
            active INTEGER DEFAULT 1,
            synced_at TEXT
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_plate ON vehicles(plate)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_active ON vehicles(active)')

    # Barrier mapping table (LOCAL config)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS barrier_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_ip TEXT NOT NULL,
            camera_name TEXT,
            relay_channels TEXT NOT NULL,
            direction TEXT DEFAULT 'both',
            location_name TEXT,
            location_id INTEGER,
            active INTEGER DEFAULT 1
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_camera_ip ON barrier_mapping(camera_ip)')

    # Access logs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS access_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plate TEXT NOT NULL,
            camera_ip TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            access_granted INTEGER,
            vehicle_type TEXT,
            unit_name TEXT,
            owner_name TEXT,
            image_path TEXT,
            s3_url TEXT,
            odoo_synced INTEGER DEFAULT 0,
            odoo_log_id INTEGER
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_log_timestamp ON access_logs(timestamp)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_log_synced ON access_logs(odoo_synced)')

    # Locations table (synced from Odoo site.location)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            odoo_id INTEGER UNIQUE,
            site_id INTEGER,
            name TEXT,
            code TEXT,
            camera_ip_address TEXT,
            parent_id INTEGER,
            active INTEGER DEFAULT 1,
            synced_at TEXT
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_location_site ON locations(site_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_location_active ON locations(active)')

    # ANPR Cameras table (synced from Odoo location.devices.anprfeed)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS anpr_cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            odoo_id INTEGER UNIQUE,
            location_id INTEGER,
            site_id INTEGER,
            name TEXT,
            reg_code TEXT,
            reg_password TEXT,
            ip_address TEXT,
            relay_channels TEXT,
            active INTEGER DEFAULT 1,
            synced_at TEXT,
            FOREIGN KEY (location_id) REFERENCES locations(odoo_id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_anpr_location ON anpr_cameras(location_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_anpr_site ON anpr_cameras(site_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_anpr_active ON anpr_cameras(active)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_anpr_reg_code ON anpr_cameras(reg_code)')

    # Migration: Add relay_channels column if not exists
    try:
        cursor.execute('ALTER TABLE anpr_cameras ADD COLUMN relay_channels TEXT')
    except:
        pass  # Column already exists

    # Upload queue table (for offline resilience)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS upload_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            retries INTEGER DEFAULT 0,
            last_error TEXT
        )
    ''')

    conn.commit()


@contextmanager
def db_transaction():
    """Context manager for database transactions"""
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
