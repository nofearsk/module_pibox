"""
Odoo Sync Service
Handles synchronization with Odoo server via REST API
"""
import json
import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)


class SyncService:
    """Service for syncing data with Odoo"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._sync_thread = None
        self._running = False
        self.last_sync = None
        self.last_error = None

    def _get_config(self):
        """Get current config"""
        from config import config
        return config

    def _get_api(self):
        """Get Odoo API client"""
        from services.odoo_api import odoo_api
        return odoo_api

    @property
    def odoo_connected(self):
        """Check if Odoo is connected"""
        return self._get_api().connected

    def test_connection(self):
        """Test connection to Odoo"""
        api = self._get_api()
        return api.test_connection()

    def sync_vehicles(self):
        """Sync vehicles from Odoo"""
        try:
            cfg = self._get_config()
            api = self._get_api()

            if not cfg.is_configured:
                raise Exception("Odoo not configured - please login first")

            # Get vehicles from Odoo
            site_id = int(cfg.site_id) if cfg.site_id else None
            vehicles = api.get_vehicles(site_id=site_id, active_only=True)

            # Sync to local database
            from database.models import VehicleModel

            # Transform vehicle data to expected format
            # Odoo field mapping:
            # - name: This is actually the plate number in Odoo (e.g., "XE5839D")
            # - vehicle_number: Alternative plate field (use as fallback)
            # - iunumber: IU number
            # - unit_id: [id, name] tuple
            # - validfrom/validto: validity dates
            vehicle_list = []
            for v in vehicles:
                # Use name as plate (primary), fall back to vehicle_number
                plate = v.get('name', '') or v.get('vehicle_number', '')
                if plate:
                    plate = str(plate).strip().upper().replace(' ', '')

                vehicle_list.append({
                    'id': v.get('id'),
                    'plate': plate,
                    'iu_number': v.get('iunumber'),
                    'unit_id': v.get('unit_id')[0] if isinstance(v.get('unit_id'), (list, tuple)) else v.get('unit_id'),
                    'unit_name': v.get('unit_id')[1] if isinstance(v.get('unit_id'), (list, tuple)) else None,
                    'owner_name': None,  # Not provided in units.vehicles
                    'valid_from': v.get('validfrom'),
                    'valid_to': v.get('validto'),
                })

            count = VehicleModel.sync_from_odoo(vehicle_list)
            self.last_sync = datetime.now()
            self.last_error = None

            logger.info(f"Synced {count} vehicles from Odoo")
            return count

        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Vehicle sync failed: {e}")
            raise

    def sync_locations(self):
        """Sync locations from Odoo"""
        try:
            cfg = self._get_config()
            api = self._get_api()

            if not cfg.is_configured:
                raise Exception("Odoo not configured - please login first")

            # Get locations from Odoo
            site_id = int(cfg.site_id) if cfg.site_id else None
            locations = api.get_locations(site_id=site_id, active_only=True)

            # Sync to local database
            from database.models import LocationModel
            count = LocationModel.sync_from_odoo(locations)

            logger.info(f"Synced {count} locations from Odoo")
            return count

        except Exception as e:
            logger.error(f"Location sync failed: {e}")
            raise

    def sync_anpr_cameras(self):
        """Sync ANPR cameras from Odoo"""
        try:
            cfg = self._get_config()
            api = self._get_api()

            if not cfg.is_configured:
                raise Exception("Odoo not configured - please login first")

            # Get ANPR cameras from Odoo
            site_id = int(cfg.site_id) if cfg.site_id else None
            cameras = api.get_anpr_cameras(site_id=site_id, active_only=True)

            # Sync to local database
            from database.models import AnprCameraModel
            count = AnprCameraModel.sync_from_odoo(cameras)

            logger.info(f"Synced {count} ANPR cameras from Odoo")
            return count

        except Exception as e:
            logger.error(f"ANPR camera sync failed: {e}")
            raise

    def sync_all(self):
        """Sync all data from Odoo"""
        results = {
            'vehicles': 0,
            'locations': 0,
            'anpr_cameras': 0,
            'errors': []
        }

        try:
            results['locations'] = self.sync_locations()
        except Exception as e:
            results['errors'].append(f"Locations: {e}")

        try:
            results['anpr_cameras'] = self.sync_anpr_cameras()
        except Exception as e:
            results['errors'].append(f"ANPR Cameras: {e}")

        try:
            results['vehicles'] = self.sync_vehicles()
        except Exception as e:
            results['errors'].append(f"Vehicles: {e}")

        return results

    def push_access_log(self, log_data):
        """Push access log to Odoo (vehicle.anpr.log)"""
        try:
            api = self._get_api()
            cfg = self._get_config()

            log_id = api.create_access_log(
                plate=log_data['plate'],
                timestamp=log_data['timestamp'],
                access_granted=log_data.get('access_granted'),
                vehicle_type=log_data.get('vehicle_type'),
                site_id=cfg.site_id if cfg.site_id else None,
                location_id=log_data.get('location_id'),
                plate_image_url=log_data.get('plate_image_url'),
                vehicle_image_url=log_data.get('vehicle_image_url'),
                unit_id=log_data.get('unit_id'),
                iu_number=log_data.get('iu_number')
            )

            return log_id

        except Exception as e:
            logger.error(f"Failed to push access log: {e}")
            # Queue for retry
            from database.models import UploadQueueModel
            UploadQueueModel.add('odoo_log', log_data)
            raise

    def push_access_log_async(self, log_id, plate, camera_ip, access_granted, vehicle_type,
                              plate_image_url=None, vehicle_image_url=None, location_name=None,
                              location_id=None, unit_id=None, iu_number=None):
        """Push access log asynchronously

        Args:
            location_id: Odoo location ID (from camera reg_code lookup)
        """
        def push_thread():
            try:
                cfg = self._get_config()

                # location_id should be provided from camera reg_code lookup
                # No need to look it up from barrier_mapping anymore

                log_data = {
                    'plate': plate,
                    'timestamp': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),  # UTC time for Odoo
                    'access_granted': access_granted,
                    'vehicle_type': vehicle_type,
                    'plate_image_url': plate_image_url,
                    'vehicle_image_url': vehicle_image_url,
                    'site_id': cfg.site_id,
                    'location_id': location_id,
                    'unit_id': unit_id,
                    'iu_number': iu_number
                }
                odoo_log_id = self.push_access_log(log_data)

                # Update local record with Odoo ID
                from database.models import AccessLogModel
                AccessLogModel.mark_synced(log_id, odoo_log_id)
                logger.info(f"Access log {log_id} synced to Odoo as {odoo_log_id} (location_id: {location_id})")

            except Exception as e:
                logger.error(f"Async push failed: {e}")

        thread = threading.Thread(target=push_thread, daemon=True)
        thread.start()

    def process_queue(self):
        """Process pending items in upload queue"""
        from database.models import UploadQueueModel

        # Process Odoo logs
        pending = UploadQueueModel.get_pending('odoo_log', limit=20)
        for item in pending:
            try:
                payload = json.loads(item['payload'])

                # Normalize timestamp to Odoo format if needed
                if 'timestamp' in payload and payload['timestamp']:
                    ts = payload['timestamp']
                    # Handle ISO format with T separator and microseconds
                    if 'T' in ts:
                        try:
                            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                            payload['timestamp'] = dt.strftime('%Y-%m-%d %H:%M:%S')
                        except ValueError:
                            pass  # Keep original if parsing fails

                self.push_access_log(payload)
                UploadQueueModel.mark_completed(item['id'])
            except Exception as e:
                UploadQueueModel.mark_failed(item['id'], e)

    def start_sync_loop(self, interval=None):
        """Start background sync loop"""
        if self._running:
            return

        cfg = self._get_config()

        # Don't start if not configured
        if not cfg.is_configured:
            logger.warning("Sync loop not started - Odoo not configured")
            return

        self._running = True

        def sync_loop():
            cfg = self._get_config()
            sync_interval = interval or cfg.sync_interval

            # Initial delay before first sync
            time.sleep(10)

            while self._running:
                try:
                    # Sync all data (locations, ANPR cameras, vehicles)
                    results = self.sync_all()
                    if results['errors']:
                        logger.warning(f"Sync completed with errors: {results['errors']}")

                    # Process queue
                    self.process_queue()

                except Exception as e:
                    logger.error(f"Sync loop error: {e}")

                # Wait for next sync
                for _ in range(sync_interval):
                    if not self._running:
                        break
                    time.sleep(1)

        self._sync_thread = threading.Thread(target=sync_loop, daemon=True)
        self._sync_thread.start()
        logger.info(f"Sync loop started (interval: {cfg.sync_interval}s)")

    def stop_sync_loop(self):
        """Stop background sync loop"""
        self._running = False
        if self._sync_thread:
            self._sync_thread.join(timeout=5)
        logger.info("Sync loop stopped")

    def force_sync(self):
        """Force immediate sync"""
        def sync_thread():
            try:
                results = self.sync_all()
                self.process_queue()
                logger.info(f"Force sync completed: {results}")
            except Exception as e:
                logger.error(f"Force sync failed: {e}")

        thread = threading.Thread(target=sync_thread, daemon=True)
        thread.start()

    def get_status(self):
        """Get sync status"""
        from database.models import VehicleModel, LocationModel, AnprCameraModel, UploadQueueModel
        api = self._get_api()

        return {
            'odoo_connected': api.connected,
            'odoo_url': api._base_url or '',
            'odoo_username': api._username or '',
            'last_sync': self.last_sync.isoformat() if self.last_sync else None,
            'last_error': self.last_error,
            'vehicles_count': VehicleModel.count(),
            'locations_count': LocationModel.count(),
            'anpr_cameras_count': AnprCameraModel.count(),
            'queue_pending': UploadQueueModel.count_pending()
        }


# Singleton instance
sync_service = SyncService()