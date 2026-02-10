"""
Odoo JSON-RPC API Client
Uses Odoo's native JSON-RPC API (works on any Odoo without extra modules)
"""
import requests
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class OdooAPIError(Exception):
    """Exception for Odoo API errors"""
    pass


class OdooAPI:
    """
    Client for Odoo using native JSON-RPC API

    Endpoints:
    - /web/session/authenticate - Login and get session
    - /web/session/destroy - Logout
    - /web/dataset/call_kw - Call model methods (search, read, create, write, unlink)
    """

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
        self._session = None
        self._base_url = None
        self._db = None
        self._uid = None
        self._username = None
        self.connected = False
        self.last_error = None

    def _get_config(self):
        """Get current config from database"""
        from config import config
        return config

    def _load_credentials(self):
        """Load stored credentials from config"""
        # Skip if we already have a valid session
        if self._session and self._uid and self._base_url:
            return

        cfg = self._get_config()
        self._base_url = cfg.get('odoo_url', '')
        self._db = cfg.get('odoo_db', '')
        self._username = cfg.get('odoo_username', '')
        self._uid = int(cfg.get('odoo_uid', 0)) if cfg.get('odoo_uid') else None
        session_id = cfg.get('odoo_session_id', '')

        if session_id and self._base_url and not self._session:
            # Restore session from saved cookie
            self._session = requests.Session()
            self._session.cookies.set('session_id', session_id)

    def _save_session(self, password=None):
        """Save session to config database"""
        cfg = self._get_config()
        cfg.set('odoo_url', self._base_url or '')
        cfg.set('odoo_db', self._db or '')
        cfg.set('odoo_username', self._username or '')
        cfg.set('odoo_uid', str(self._uid) if self._uid else '')

        # Save password for auto-relogin
        if password:
            cfg.set('odoo_password', password)

        # Save session cookie
        session_id = ''
        if self._session:
            session_id = self._session.cookies.get('session_id', '')
        cfg.set('odoo_session_id', session_id)

    def _jsonrpc(self, endpoint, params):
        """
        Make JSON-RPC request to Odoo

        Args:
            endpoint: API endpoint (e.g., '/web/session/authenticate')
            params: Parameters for the RPC call

        Returns:
            dict: Result from Odoo
        """
        if not self._base_url:
            self._load_credentials()
        if not self._base_url:
            raise OdooAPIError("Odoo URL not configured")

        if not self._session:
            self._session = requests.Session()

        url = f"{self._base_url.rstrip('/')}{endpoint}"

        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": params,
            "id": 1
        }

        try:
            response = self._session.post(
                url,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            response.raise_for_status()
            result = response.json()

            if 'error' in result:
                error = result['error']
                error_msg = error.get('data', {}).get('message', error.get('message', 'Unknown error'))
                self.last_error = error_msg
                raise OdooAPIError(error_msg)

            self.connected = True
            self.last_error = None
            return result.get('result')

        except requests.exceptions.Timeout:
            self.connected = False
            self.last_error = "Connection timeout"
            raise OdooAPIError("Connection timeout - server not responding")

        except requests.exceptions.ConnectionError as e:
            self.connected = False
            self.last_error = f"Connection error: {e}"
            raise OdooAPIError(f"Cannot connect to Odoo server: {e}")

        except requests.exceptions.HTTPError as e:
            self.connected = False
            self.last_error = f"HTTP error: {e}"
            raise OdooAPIError(f"HTTP error: {e}")

    def _relogin(self):
        """Auto-relogin using stored credentials"""
        cfg = self._get_config()
        password = cfg.get('odoo_password', '')

        if not password or not self._base_url or not self._username:
            logger.warning("Cannot auto-relogin - missing stored credentials")
            return False

        try:
            logger.info(f"Session expired, auto-relogin as {self._username}")
            self._session = requests.Session()

            result = self._jsonrpc('/web/session/authenticate', {
                'db': self._db,
                'login': self._username,
                'password': password
            })

            if result and result.get('uid'):
                self._uid = result['uid']
                self.connected = True
                self._save_session(password=password)
                logger.info(f"Auto-relogin successful")
                return True
            return False
        except Exception as e:
            logger.error(f"Auto-relogin failed: {e}")
            return False

    def _call_kw(self, model, method, args=None, kwargs=None, _retry=True):
        """
        Call a method on an Odoo model

        Args:
            model: Model name (e.g., 'res.partner')
            method: Method name (e.g., 'search_read')
            args: Positional arguments
            kwargs: Keyword arguments
            _retry: Internal flag to prevent infinite retry loops

        Returns:
            Method result
        """
        if not self._uid:
            self._load_credentials()
        if not self._uid:
            raise OdooAPIError("Not authenticated - please login first")

        try:
            return self._jsonrpc('/web/dataset/call_kw', {
                'model': model,
                'method': method,
                'args': args or [],
                'kwargs': kwargs or {}
            })
        except OdooAPIError as e:
            error_msg = str(e).lower()
            # Check for session expired errors
            if _retry and ('session' in error_msg or 'expired' in error_msg or 'invalid' in error_msg):
                if self._relogin():
                    # Retry the call after successful relogin
                    return self._call_kw(model, method, args, kwargs, _retry=False)
            raise

    # ==================== Authentication ====================

    def login(self, odoo_url, username, password, db=None):
        """
        Authenticate with Odoo

        Args:
            odoo_url: Base URL of Odoo server (e.g., https://myodoo.com)
            username: Odoo username/login
            password: Odoo password
            db: Database name (optional, will try to detect)

        Returns:
            dict: {'success': True, 'uid': ..., 'username': ...}
        """
        self._base_url = odoo_url.rstrip('/')
        self._session = requests.Session()

        # If db not provided, try to get it from database list
        if not db:
            try:
                result = self._jsonrpc('/web/database/list', {})
                if result and len(result) == 1:
                    db = result[0]
                elif result and len(result) > 1:
                    raise OdooAPIError(f"Multiple databases found: {result}. Please specify database name.")
                else:
                    raise OdooAPIError("No database found")
            except OdooAPIError:
                raise
            except Exception as e:
                raise OdooAPIError(f"Failed to get database list: {e}")

        self._db = db

        try:
            result = self._jsonrpc('/web/session/authenticate', {
                'db': db,
                'login': username,
                'password': password
            })

            if result and result.get('uid'):
                self._uid = result['uid']
                self._username = result.get('username', username)
                self.connected = True
                self.last_error = None

                # Save session with password for auto-relogin
                self._save_session(password=password)

                logger.info(f"Successfully logged in as {self._username} (uid: {self._uid})")

                return {
                    'success': True,
                    'uid': self._uid,
                    'username': self._username,
                    'db': self._db
                }
            else:
                raise OdooAPIError("Login failed - invalid credentials")

        except OdooAPIError:
            raise
        except Exception as e:
            self.connected = False
            self.last_error = str(e)
            raise OdooAPIError(f"Login failed: {e}")

    def logout(self):
        """Destroy session and logout"""
        try:
            if self._session and self._uid:
                self._jsonrpc('/web/session/destroy', {})
        except Exception:
            pass  # Ignore errors on logout

        # Clear stored credentials
        cfg = self._get_config()
        cfg.set('odoo_session_id', '')
        cfg.set('odoo_uid', '')

        self._session = None
        self._uid = None
        self.connected = False
        logger.info("Logged out successfully")

    def test_connection(self):
        """
        Test if connection to Odoo is working

        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            self._load_credentials()

            if not self._base_url:
                return False, "Odoo URL not configured"
            if not self._uid:
                return False, "Not authenticated - please login"

            # Try to read current user
            result = self._call_kw('res.users', 'read', [[self._uid]], {'fields': ['name']})
            if result:
                self.connected = True
                return True, f"Connected as {result[0].get('name', 'Unknown')}"
            return False, "Connection test failed"

        except OdooAPIError as e:
            self.connected = False
            return False, str(e)
        except Exception as e:
            self.connected = False
            return False, f"Connection test failed: {e}"

    # ==================== CRUD Operations ====================

    def search_read(self, model, domain=None, fields=None, offset=0, limit=100, order=None):
        """
        Search and read records

        Args:
            model: Model name (e.g., 'res.partner')
            domain: Search domain (list of tuples)
            fields: List of field names to return
            offset: Record offset for pagination
            limit: Maximum records to return
            order: Sort order (e.g., 'name asc')

        Returns:
            list: List of record dictionaries
        """
        kwargs = {
            'domain': domain or [],
            'fields': fields or ['id', 'name'],
            'offset': offset,
            'limit': limit,
        }
        if order:
            kwargs['order'] = order

        return self._call_kw(model, 'search_read', [], kwargs)

    def search(self, model, domain=None, offset=0, limit=100, order=None):
        """Search for record IDs"""
        kwargs = {
            'offset': offset,
            'limit': limit,
        }
        if order:
            kwargs['order'] = order

        return self._call_kw(model, 'search', [domain or []], kwargs)

    def read(self, model, ids, fields=None):
        """Read records by IDs"""
        return self._call_kw(model, 'read', [ids], {'fields': fields or []})

    def create(self, model, values):
        """Create a new record"""
        return self._call_kw(model, 'create', [values])

    def write(self, model, ids, values):
        """Update records"""
        return self._call_kw(model, 'write', [ids, values])

    def unlink(self, model, ids):
        """Delete records"""
        return self._call_kw(model, 'unlink', [ids])

    # ==================== Convenience Methods ====================

    def get_vehicles(self, site_id=None, active_only=True):
        """
        Get vehicles, optionally filtered by site

        Args:
            site_id: Filter by site ID (optional)
            active_only: Only return active vehicles

        Returns:
            list: Vehicle records
        """
        domain = []

        if active_only:
            domain.append(('active', '=', True))

        if site_id:
            domain.append(('site_id', '=', int(site_id)))

        return self.search_read(
            'units.vehicles',
            domain=domain,
            fields=['id', 'vehicle_number', 'iunumber', 'unit_id', 'name',
                    'validfrom', 'validto', 'active']
        )

    def get_locations(self, site_id=None, active_only=True):
        """
        Get locations from site.location model

        Args:
            site_id: Filter by site ID (optional)
            active_only: Only return active locations

        Returns:
            list: Location records
        """
        domain = []

        if active_only:
            domain.append(('active', '=', True))

        if site_id:
            domain.append(('site_id', '=', int(site_id)))

        return self.search_read(
            'site.location',
            domain=domain,
            fields=['id', 'site_id', 'name', 'code', 'camera_ip_address',
                    'parent_id', 'active'],
            limit=500
        )

    def get_anpr_cameras(self, site_id=None, active_only=True):
        """
        Get ANPR cameras from location.devices.anprfeed model

        Args:
            site_id: Filter by site ID (optional)
            active_only: Only return active cameras

        Returns:
            list: ANPR camera records
        """
        domain = []

        if active_only:
            domain.append(('active', '=', True))

        if site_id:
            domain.append(('site_id', '=', int(site_id)))

        return self.search_read(
            'location.devices.anprfeed',
            domain=domain,
            fields=['id', 'location_id', 'site_id', 'name', 'reg_code',
                    'reg_password', 'active'],
            limit=500
        )

    def create_access_log(self, plate, timestamp, access_granted, vehicle_type,
                          camera_ip=None, location_name=None, site_id=None,
                          plate_image_url=None, vehicle_image_url=None,
                          location_id=None, unit_id=None, iu_number=None):
        """
        Create an access log entry in Odoo (vehicle.anpr.log)

        Model fields:
        - name: Vehicle Number (plate)
        - logtime: Log Date and Time
        - site_id: Site (REQUIRED)
        - location_id: Location
        - plate_image_url: Plate Image URL
        - vehicle_image_url: Vehicle Image URL
        - iunumber: IU Number
        - unit_id: Unit

        Returns:
            int: Created log ID
        """
        # site_id is REQUIRED
        if not site_id:
            cfg = self._get_config()
            site_id = cfg.get('site_id')

        if not site_id:
            raise OdooAPIError("site_id is required for vehicle.anpr.log")

        values = {
            'name': plate,  # Vehicle Number
            'logtime': timestamp,  # Log Date and Time
            'site_id': int(site_id),  # Site (REQUIRED)
        }

        if location_id:
            values['location_id'] = int(location_id)
        if plate_image_url:
            values['plate_image_url'] = plate_image_url
        if vehicle_image_url:
            values['vehicle_image_url'] = vehicle_image_url
        if iu_number:
            values['iunumber'] = iu_number
        if unit_id:
            values['unit_id'] = int(unit_id)

        return self.create('vehicle.anpr.log', values)

    def get_status(self):
        """Get connection status info"""
        return {
            'connected': self.connected,
            'url': self._base_url or '',
            'db': self._db or '',
            'username': self._username or '',
            'uid': self._uid,
            'has_session': bool(self._session and self._uid),
            'last_error': self.last_error
        }

    @property
    def is_configured(self):
        """Check if Odoo is configured and authenticated"""
        if not self._uid:
            self._load_credentials()
        return bool(self._base_url and self._uid)


# Singleton instance
odoo_api = OdooAPI()
