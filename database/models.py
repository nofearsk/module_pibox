"""
Data Access Layer Models
"""
import json
from datetime import datetime, date, timedelta
from .db import get_db


class VehicleModel:
    """Data access for vehicles table"""

    @staticmethod
    def get_all(active_only=True):
        """Get all vehicles"""
        db = get_db()
        query = 'SELECT * FROM vehicles'
        if active_only:
            query += ' WHERE active = 1'
        query += ' ORDER BY plate'
        return db.execute(query).fetchall()

    @staticmethod
    def get_by_plate(plate):
        """Get vehicle by plate number (case-insensitive)"""
        db = get_db()
        return db.execute(
            'SELECT * FROM vehicles WHERE UPPER(plate) = UPPER(?) AND active = 1',
            (plate,)
        ).fetchone()

    @staticmethod
    def search(query, limit=50):
        """Search vehicles by plate"""
        db = get_db()
        return db.execute(
            'SELECT * FROM vehicles WHERE UPPER(plate) LIKE UPPER(?) AND active = 1 ORDER BY plate LIMIT ?',
            (f'%{query}%', limit)
        ).fetchall()

    @staticmethod
    def count(search=None):
        """Count active vehicles"""
        db = get_db()
        if search:
            result = db.execute(
                'SELECT COUNT(*) as cnt FROM vehicles WHERE active = 1 AND UPPER(plate) LIKE UPPER(?)',
                (f'%{search}%',)
            ).fetchone()
        else:
            result = db.execute('SELECT COUNT(*) as cnt FROM vehicles WHERE active = 1').fetchone()
        return result['cnt'] if result else 0

    @staticmethod
    def get_paginated(page=1, per_page=50, search=None):
        """Get vehicles with pagination"""
        db = get_db()
        offset = (page - 1) * per_page

        if search:
            return db.execute(
                'SELECT * FROM vehicles WHERE active = 1 AND UPPER(plate) LIKE UPPER(?) ORDER BY plate LIMIT ? OFFSET ?',
                (f'%{search}%', per_page, offset)
            ).fetchall()
        else:
            return db.execute(
                'SELECT * FROM vehicles WHERE active = 1 ORDER BY plate LIMIT ? OFFSET ?',
                (per_page, offset)
            ).fetchall()

    @staticmethod
    def sync_from_odoo(vehicles):
        """Sync vehicles from Odoo (full replace)"""
        db = get_db()
        now = datetime.now().isoformat()

        # Mark all as inactive first
        db.execute('UPDATE vehicles SET active = 0')

        # Upsert each vehicle
        for v in vehicles:
            db.execute('''
                INSERT INTO vehicles (odoo_id, plate, iu_number, unit_id, unit_name,
                                      owner_name, valid_from, valid_to, active, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(odoo_id) DO UPDATE SET
                    plate = excluded.plate,
                    iu_number = excluded.iu_number,
                    unit_id = excluded.unit_id,
                    unit_name = excluded.unit_name,
                    owner_name = excluded.owner_name,
                    valid_from = excluded.valid_from,
                    valid_to = excluded.valid_to,
                    active = 1,
                    synced_at = excluded.synced_at
            ''', (
                v.get('id'),
                (v.get('plate') or '').upper(),
                v.get('iu_number') or None,
                v.get('unit_id'),
                v.get('unit_name'),
                v.get('owner_name') or None,
                v.get('valid_from') or None,
                v.get('valid_to') or None,
                now
            ))

        db.commit()
        return len(vehicles)

    @staticmethod
    def is_valid(vehicle_row):
        """Check if vehicle is currently valid (within date range)"""
        if not vehicle_row:
            return False
        if not vehicle_row['active']:
            return False

        today = date.today().isoformat()

        valid_from = vehicle_row['valid_from']
        valid_to = vehicle_row['valid_to']

        if valid_from and today < valid_from:
            return False
        if valid_to and today > valid_to:
            return False

        return True


class BarrierModel:
    """Data access for barrier_mapping table"""

    @staticmethod
    def get_all(active_only=True):
        """Get all barrier mappings"""
        db = get_db()
        query = 'SELECT * FROM barrier_mapping'
        if active_only:
            query += ' WHERE active = 1'
        return db.execute(query).fetchall()

    @staticmethod
    def get_by_camera_ip(camera_ip):
        """Get barrier mapping for a camera IP"""
        db = get_db()
        return db.execute(
            'SELECT * FROM barrier_mapping WHERE camera_ip = ? AND active = 1',
            (camera_ip,)
        ).fetchone()

    @staticmethod
    def get_relay_channels(camera_ip):
        """Get relay channels for a camera IP as list"""
        mapping = BarrierModel.get_by_camera_ip(camera_ip)
        if mapping:
            try:
                return json.loads(mapping['relay_channels'])
            except (json.JSONDecodeError, TypeError):
                return [int(mapping['relay_channels'])]
        return []  # No relay configured - don't trigger for unknown cameras

    @staticmethod
    def create(camera_ip, relay_channels, camera_name=None, direction='both', location_name=None, location_id=None):
        """Create new barrier mapping"""
        db = get_db()
        if isinstance(relay_channels, list):
            relay_channels = json.dumps(relay_channels)
        db.execute('''
            INSERT INTO barrier_mapping (camera_ip, camera_name, relay_channels, direction, location_name, location_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (camera_ip, camera_name, relay_channels, direction, location_name, location_id))
        db.commit()
        return db.execute('SELECT last_insert_rowid()').fetchone()[0]

    @staticmethod
    def update(mapping_id, **kwargs):
        """Update barrier mapping"""
        db = get_db()
        allowed = ['camera_ip', 'camera_name', 'relay_channels', 'direction', 'location_name', 'location_id', 'active']
        updates = []
        values = []
        for key, value in kwargs.items():
            if key in allowed:
                if key == 'relay_channels' and isinstance(value, list):
                    value = json.dumps(value)
                updates.append(f'{key} = ?')
                values.append(value)

        if updates:
            values.append(mapping_id)
            db.execute(f'UPDATE barrier_mapping SET {", ".join(updates)} WHERE id = ?', values)
            db.commit()

    @staticmethod
    def delete(mapping_id):
        """Delete barrier mapping"""
        db = get_db()
        db.execute('DELETE FROM barrier_mapping WHERE id = ?', (mapping_id,))
        db.commit()


class AccessLogModel:
    """Data access for access_logs table"""

    @staticmethod
    def create(plate, camera_ip, access_granted, vehicle_type, unit_name=None,
               owner_name=None, image_path=None, camera_name=None, relay_triggered=None):
        """Create new access log entry"""
        db = get_db()
        # Convert relay list to string if needed
        if isinstance(relay_triggered, list):
            relay_triggered = ','.join(str(r) for r in relay_triggered) if relay_triggered else None
        db.execute('''
            INSERT INTO access_logs (plate, camera_ip, camera_name, relay_triggered,
                                     access_granted, vehicle_type, unit_name, owner_name,
                                     image_path, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (plate, camera_ip, camera_name, relay_triggered,
              1 if access_granted else 0, vehicle_type, unit_name, owner_name,
              image_path, datetime.now().isoformat()))
        db.commit()
        return db.execute('SELECT last_insert_rowid()').fetchone()[0]

    @staticmethod
    def get_recent(limit=50, vehicle_type=None):
        """Get recent access logs"""
        db = get_db()
        query = 'SELECT * FROM access_logs'
        params = []
        if vehicle_type:
            query += ' WHERE vehicle_type = ?'
            params.append(vehicle_type)
        query += ' ORDER BY timestamp DESC LIMIT ?'
        params.append(limit)
        return db.execute(query, params).fetchall()

    @staticmethod
    def count(vehicle_type=None, search=None, date_from=None, date_to=None, access_granted=None):
        """Count access logs"""
        db = get_db()
        query = 'SELECT COUNT(*) as cnt FROM access_logs WHERE 1=1'
        params = []
        if vehicle_type:
            query += ' AND vehicle_type = ?'
            params.append(vehicle_type)
        if search:
            query += ' AND (UPPER(plate) LIKE UPPER(?) OR UPPER(camera_name) LIKE UPPER(?))'
            params.extend([f'%{search}%', f'%{search}%'])
        if date_from:
            query += ' AND date(timestamp) >= ?'
            params.append(date_from)
        if date_to:
            query += ' AND date(timestamp) <= ?'
            params.append(date_to)
        if access_granted is not None:
            query += ' AND access_granted = ?'
            params.append(1 if access_granted else 0)
        result = db.execute(query, params).fetchone()
        return result['cnt'] if result else 0

    @staticmethod
    def get_paginated(page=1, per_page=50, vehicle_type=None, search=None, date_from=None, date_to=None, access_granted=None):
        """Get access logs with pagination"""
        db = get_db()
        offset = (page - 1) * per_page
        query = 'SELECT * FROM access_logs WHERE 1=1'
        params = []
        if vehicle_type:
            query += ' AND vehicle_type = ?'
            params.append(vehicle_type)
        if search:
            query += ' AND (UPPER(plate) LIKE UPPER(?) OR UPPER(camera_name) LIKE UPPER(?))'
            params.extend([f'%{search}%', f'%{search}%'])
        if date_from:
            query += ' AND date(timestamp) >= ?'
            params.append(date_from)
        if date_to:
            query += ' AND date(timestamp) <= ?'
            params.append(date_to)
        if access_granted is not None:
            query += ' AND access_granted = ?'
            params.append(1 if access_granted else 0)
        query += ' ORDER BY timestamp DESC LIMIT ? OFFSET ?'
        params.extend([per_page, offset])
        return db.execute(query, params).fetchall()

    @staticmethod
    def get_stats_by_date_range(date_from, date_to):
        """Get statistics for a date range"""
        db = get_db()
        result = db.execute('''
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN access_granted = 1 THEN 1 ELSE 0 END) as granted,
                SUM(CASE WHEN access_granted = 0 THEN 1 ELSE 0 END) as denied,
                SUM(CASE WHEN vehicle_type = 'resident' THEN 1 ELSE 0 END) as residents,
                SUM(CASE WHEN vehicle_type = 'unknown' THEN 1 ELSE 0 END) as unknown,
                SUM(CASE WHEN vehicle_type = 'blacklisted' THEN 1 ELSE 0 END) as blacklisted
            FROM access_logs
            WHERE date(timestamp) >= ? AND date(timestamp) <= ?
        ''', (date_from, date_to)).fetchone()
        return {
            'total': result['total'] or 0,
            'granted': result['granted'] or 0,
            'denied': result['denied'] or 0,
            'residents': result['residents'] or 0,
            'unknown': result['unknown'] or 0,
            'blacklisted': result['blacklisted'] or 0,
        }

    @staticmethod
    def get_hourly_stats(target_date=None):
        """Get hourly breakdown for a specific date"""
        db = get_db()
        if target_date is None:
            target_date = date.today().isoformat()
        result = db.execute('''
            SELECT
                strftime('%H', timestamp) as hour,
                COUNT(*) as total,
                SUM(CASE WHEN access_granted = 1 THEN 1 ELSE 0 END) as granted,
                SUM(CASE WHEN access_granted = 0 THEN 1 ELSE 0 END) as denied
            FROM access_logs
            WHERE date(timestamp) = ?
            GROUP BY strftime('%H', timestamp)
            ORDER BY hour
        ''', (target_date,)).fetchall()
        return [dict(r) for r in result]

    @staticmethod
    def get_unsynced(limit=100):
        """Get logs not yet synced to Odoo"""
        db = get_db()
        return db.execute(
            'SELECT * FROM access_logs WHERE odoo_synced = 0 ORDER BY timestamp LIMIT ?',
            (limit,)
        ).fetchall()

    @staticmethod
    def mark_synced(log_id, odoo_log_id):
        """Mark log as synced to Odoo"""
        db = get_db()
        db.execute(
            'UPDATE access_logs SET odoo_synced = 1, odoo_log_id = ? WHERE id = ?',
            (odoo_log_id, log_id)
        )
        db.commit()

    @staticmethod
    def update_s3_url(log_id, s3_url):
        """Update S3 URL after upload"""
        db = get_db()
        db.execute('UPDATE access_logs SET s3_url = ? WHERE id = ?', (s3_url, log_id))
        db.commit()

    @staticmethod
    def get_today_stats():
        """Get today's statistics"""
        db = get_db()
        today = date.today().isoformat()
        result = db.execute('''
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN access_granted = 1 THEN 1 ELSE 0 END) as granted,
                SUM(CASE WHEN access_granted = 0 THEN 1 ELSE 0 END) as denied,
                SUM(CASE WHEN vehicle_type = 'resident' THEN 1 ELSE 0 END) as residents,
                SUM(CASE WHEN vehicle_type = 'unknown' THEN 1 ELSE 0 END) as unknown
            FROM access_logs
            WHERE date(timestamp) = ?
        ''', (today,)).fetchone()
        return {
            'total': result['total'] or 0,
            'granted': result['granted'] or 0,
            'denied': result['denied'] or 0,
            'residents': result['residents'] or 0,
            'unknown': result['unknown'] or 0,
        }

    @staticmethod
    def get_by_id(log_id):
        """Get log by ID"""
        db = get_db()
        return db.execute('SELECT * FROM access_logs WHERE id = ?', (log_id,)).fetchone()


class UploadQueueModel:
    """Data access for upload_queue table"""

    @staticmethod
    def add(queue_type, payload):
        """Add item to queue"""
        db = get_db()
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        db.execute(
            'INSERT INTO upload_queue (queue_type, payload) VALUES (?, ?)',
            (queue_type, payload)
        )
        db.commit()

    @staticmethod
    def get_pending(queue_type=None, limit=50):
        """Get pending items from queue"""
        db = get_db()
        query = 'SELECT * FROM upload_queue WHERE retries < 5'
        params = []
        if queue_type:
            query += ' AND queue_type = ?'
            params.append(queue_type)
        query += ' ORDER BY created_at LIMIT ?'
        params.append(limit)
        return db.execute(query, params).fetchall()

    @staticmethod
    def mark_completed(queue_id):
        """Remove item from queue"""
        db = get_db()
        db.execute('DELETE FROM upload_queue WHERE id = ?', (queue_id,))
        db.commit()

    @staticmethod
    def mark_failed(queue_id, error):
        """Increment retry count and store error"""
        db = get_db()
        db.execute(
            'UPDATE upload_queue SET retries = retries + 1, last_error = ? WHERE id = ?',
            (str(error), queue_id)
        )
        db.commit()

    @staticmethod
    def count_pending():
        """Count pending items"""
        db = get_db()
        result = db.execute('SELECT COUNT(*) as cnt FROM upload_queue WHERE retries < 5').fetchone()
        return result['cnt'] if result else 0


class LocationModel:
    """Data access for locations table"""

    @staticmethod
    def get_all(active_only=True):
        """Get all locations"""
        db = get_db()
        query = 'SELECT * FROM locations'
        if active_only:
            query += ' WHERE active = 1'
        query += ' ORDER BY name'
        return db.execute(query).fetchall()

    @staticmethod
    def get_by_site(site_id, active_only=True):
        """Get locations for a specific site"""
        db = get_db()
        query = 'SELECT * FROM locations WHERE site_id = ?'
        if active_only:
            query += ' AND active = 1'
        query += ' ORDER BY name'
        return db.execute(query, (site_id,)).fetchall()

    @staticmethod
    def get_by_id(location_id):
        """Get location by ID"""
        db = get_db()
        return db.execute(
            'SELECT * FROM locations WHERE id = ?',
            (location_id,)
        ).fetchone()

    @staticmethod
    def get_by_odoo_id(odoo_id):
        """Get location by Odoo ID"""
        db = get_db()
        return db.execute(
            'SELECT * FROM locations WHERE odoo_id = ?',
            (odoo_id,)
        ).fetchone()

    @staticmethod
    def count():
        """Count active locations"""
        db = get_db()
        result = db.execute('SELECT COUNT(*) as cnt FROM locations WHERE active = 1').fetchone()
        return result['cnt'] if result else 0

    @staticmethod
    def sync_from_odoo(locations):
        """Sync locations from Odoo"""
        db = get_db()
        now = datetime.now().isoformat()

        # Mark all as inactive first
        db.execute('UPDATE locations SET active = 0')

        # Upsert each location
        for loc in locations:
            # Handle Many2one fields that come as [id, name] tuples
            site_id = loc.get('site_id')
            if isinstance(site_id, (list, tuple)):
                site_id = site_id[0]

            parent_id = loc.get('parent_id')
            if isinstance(parent_id, (list, tuple)):
                parent_id = parent_id[0]
            elif parent_id is False:
                parent_id = None

            db.execute('''
                INSERT INTO locations (odoo_id, site_id, name, code, camera_ip_address,
                                       parent_id, active, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(odoo_id) DO UPDATE SET
                    site_id = excluded.site_id,
                    name = excluded.name,
                    code = excluded.code,
                    camera_ip_address = excluded.camera_ip_address,
                    parent_id = excluded.parent_id,
                    active = 1,
                    synced_at = excluded.synced_at
            ''', (
                loc.get('id'),
                site_id,
                loc.get('name') or None,
                loc.get('code') or None,
                loc.get('camera_ip_address') or None,
                parent_id,
                now
            ))

        db.commit()
        return len(locations)


class AnprCameraModel:
    """Data access for anpr_cameras table"""

    @staticmethod
    def get_all(active_only=True):
        """Get all ANPR cameras"""
        db = get_db()
        query = 'SELECT * FROM anpr_cameras'
        if active_only:
            query += ' WHERE active = 1'
        query += ' ORDER BY name'
        return db.execute(query).fetchall()

    @staticmethod
    def get_by_location(location_id, active_only=True):
        """Get ANPR cameras for a specific location"""
        db = get_db()
        query = 'SELECT * FROM anpr_cameras WHERE location_id = ?'
        if active_only:
            query += ' AND active = 1'
        query += ' ORDER BY name'
        return db.execute(query, (location_id,)).fetchall()

    @staticmethod
    def get_by_site(site_id, active_only=True):
        """Get ANPR cameras for a specific site"""
        db = get_db()
        query = 'SELECT * FROM anpr_cameras WHERE site_id = ?'
        if active_only:
            query += ' AND active = 1'
        query += ' ORDER BY name'
        return db.execute(query, (site_id,)).fetchall()

    @staticmethod
    def get_by_id(camera_id):
        """Get camera by ID"""
        db = get_db()
        return db.execute(
            'SELECT * FROM anpr_cameras WHERE id = ?',
            (camera_id,)
        ).fetchone()

    @staticmethod
    def get_by_odoo_id(odoo_id):
        """Get camera by Odoo ID"""
        db = get_db()
        return db.execute(
            'SELECT * FROM anpr_cameras WHERE odoo_id = ?',
            (odoo_id,)
        ).fetchone()

    @staticmethod
    def get_by_reg_code(reg_code):
        """Get camera by registration code"""
        db = get_db()
        return db.execute(
            'SELECT * FROM anpr_cameras WHERE reg_code = ? AND active = 1',
            (reg_code,)
        ).fetchone()

    @staticmethod
    def count():
        """Count active cameras"""
        db = get_db()
        result = db.execute('SELECT COUNT(*) as cnt FROM anpr_cameras WHERE active = 1').fetchone()
        return result['cnt'] if result else 0

    @staticmethod
    def sync_from_odoo(cameras):
        """Sync ANPR cameras from Odoo"""
        db = get_db()
        now = datetime.now().isoformat()

        # Mark all as inactive first
        db.execute('UPDATE anpr_cameras SET active = 0')

        # Upsert each camera
        for cam in cameras:
            # Handle Many2one fields
            location_id = cam.get('location_id')
            if isinstance(location_id, (list, tuple)):
                location_id = location_id[0]
            elif location_id is False:
                location_id = None

            site_id = cam.get('site_id')
            if isinstance(site_id, (list, tuple)):
                site_id = site_id[0]

            db.execute('''
                INSERT INTO anpr_cameras (odoo_id, location_id, site_id, name, reg_code,
                                          reg_password, active, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(odoo_id) DO UPDATE SET
                    location_id = excluded.location_id,
                    site_id = excluded.site_id,
                    name = excluded.name,
                    reg_code = excluded.reg_code,
                    reg_password = excluded.reg_password,
                    active = 1,
                    synced_at = excluded.synced_at
            ''', (
                cam.get('id'),
                location_id,
                site_id,
                cam.get('name') or None,
                cam.get('reg_code') or None,
                cam.get('reg_password') or None,
                now
            ))

        db.commit()
        return len(cameras)

    @staticmethod
    def get_relay_channels(reg_code):
        """Get relay channels for a camera by reg_code"""
        camera = AnprCameraModel.get_by_reg_code(reg_code)
        if camera and camera['relay_channels']:
            try:
                return json.loads(camera['relay_channels'])
            except (json.JSONDecodeError, TypeError):
                return [int(camera['relay_channels'])]
        return []  # No relay configured

    @staticmethod
    def set_relay_channels(camera_id, relay_channels):
        """Set relay channels for a camera"""
        db = get_db()
        if isinstance(relay_channels, list):
            relay_channels = json.dumps(relay_channels)
        db.execute(
            'UPDATE anpr_cameras SET relay_channels = ? WHERE id = ?',
            (relay_channels, camera_id)
        )
        db.commit()

    @staticmethod
    def set_relay_channels_by_reg_code(reg_code, relay_channels):
        """Set relay channels for a camera by reg_code"""
        db = get_db()
        if isinstance(relay_channels, list):
            relay_channels = json.dumps(relay_channels)
        db.execute(
            'UPDATE anpr_cameras SET relay_channels = ? WHERE reg_code = ?',
            (relay_channels, reg_code)
        )
        db.commit()


class AuditLogModel:
    """Data access for audit_logs table"""

    @staticmethod
    def log(action, user=None, ip_address=None, details=None, resource_type=None, resource_id=None):
        """Create audit log entry"""
        db = get_db()
        db.execute('''
            INSERT INTO audit_logs (timestamp, action, user, ip_address, details, resource_type, resource_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (datetime.now().isoformat(), action, user, ip_address, details, resource_type, resource_id))
        db.commit()

    @staticmethod
    def get_recent(limit=100):
        """Get recent audit logs"""
        db = get_db()
        return db.execute(
            'SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ?',
            (limit,)
        ).fetchall()

    @staticmethod
    def get_paginated(page=1, per_page=50, action=None, search=None, date_from=None, date_to=None):
        """Get audit logs with pagination and filters"""
        db = get_db()
        offset = (page - 1) * per_page
        query = 'SELECT * FROM audit_logs WHERE 1=1'
        params = []

        if action:
            query += ' AND action = ?'
            params.append(action)
        if search:
            query += ' AND (user LIKE ? OR details LIKE ? OR ip_address LIKE ?)'
            params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])
        if date_from:
            query += ' AND date(timestamp) >= ?'
            params.append(date_from)
        if date_to:
            query += ' AND date(timestamp) <= ?'
            params.append(date_to)

        query += ' ORDER BY timestamp DESC LIMIT ? OFFSET ?'
        params.extend([per_page, offset])
        return db.execute(query, params).fetchall()

    @staticmethod
    def count(action=None, search=None, date_from=None, date_to=None):
        """Count audit logs"""
        db = get_db()
        query = 'SELECT COUNT(*) as cnt FROM audit_logs WHERE 1=1'
        params = []

        if action:
            query += ' AND action = ?'
            params.append(action)
        if search:
            query += ' AND (user LIKE ? OR details LIKE ? OR ip_address LIKE ?)'
            params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])
        if date_from:
            query += ' AND date(timestamp) >= ?'
            params.append(date_from)
        if date_to:
            query += ' AND date(timestamp) <= ?'
            params.append(date_to)

        result = db.execute(query, params).fetchone()
        return result['cnt'] if result else 0

    @staticmethod
    def get_actions():
        """Get distinct actions"""
        db = get_db()
        return [r['action'] for r in db.execute(
            'SELECT DISTINCT action FROM audit_logs ORDER BY action'
        ).fetchall()]


class BlacklistModel:
    """Data access for blacklist table"""

    @staticmethod
    def get_all(active_only=True):
        """Get all blacklisted plates"""
        db = get_db()
        query = 'SELECT * FROM blacklist'
        if active_only:
            query += ' WHERE active = 1'
        query += ' ORDER BY added_at DESC'
        return db.execute(query).fetchall()

    @staticmethod
    def get_by_plate(plate):
        """Check if plate is blacklisted (case-insensitive)"""
        db = get_db()
        return db.execute(
            'SELECT * FROM blacklist WHERE UPPER(plate) = UPPER(?) AND active = 1',
            (plate,)
        ).fetchone()

    @staticmethod
    def is_blacklisted(plate):
        """Check if plate is currently blacklisted"""
        entry = BlacklistModel.get_by_plate(plate)
        if not entry:
            return False
        # Check expiry
        if entry['expires_at']:
            if datetime.now().isoformat() > entry['expires_at']:
                return False
        return True

    @staticmethod
    def add(plate, reason=None, added_by=None, expires_at=None):
        """Add plate to blacklist"""
        db = get_db()
        plate = plate.upper().strip()
        try:
            db.execute('''
                INSERT INTO blacklist (plate, reason, added_by, added_at, expires_at, active)
                VALUES (?, ?, ?, ?, ?, 1)
            ''', (plate, reason, added_by, datetime.now().isoformat(), expires_at))
            db.commit()
            return True
        except Exception:
            # Already exists, update it
            db.execute('''
                UPDATE blacklist SET reason = ?, added_by = ?, added_at = ?, expires_at = ?, active = 1
                WHERE UPPER(plate) = UPPER(?)
            ''', (reason, added_by, datetime.now().isoformat(), expires_at, plate))
            db.commit()
            return True

    @staticmethod
    def remove(plate):
        """Remove plate from blacklist (soft delete)"""
        db = get_db()
        db.execute(
            'UPDATE blacklist SET active = 0 WHERE UPPER(plate) = UPPER(?)',
            (plate.upper(),)
        )
        db.commit()

    @staticmethod
    def delete(blacklist_id):
        """Hard delete blacklist entry"""
        db = get_db()
        db.execute('DELETE FROM blacklist WHERE id = ?', (blacklist_id,))
        db.commit()

    @staticmethod
    def count(active_only=True):
        """Count blacklisted plates"""
        db = get_db()
        query = 'SELECT COUNT(*) as cnt FROM blacklist'
        if active_only:
            query += ' WHERE active = 1'
        result = db.execute(query).fetchone()
        return result['cnt'] if result else 0
