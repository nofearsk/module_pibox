"""
Web Relay Service
Controls Iotzone V5+ 8-Channel Ethernet Relay Module via HTTP API

Supports:
- HTTP GET with Basic Authentication
- ON/OFF/PULSE commands for 8 relay channels
- Status polling

Default settings:
- IP: 192.168.1.166
- Username: admin
- Password: 12345678
- HTTP Port: 80
"""
import threading
import time
import logging
import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


class WebRelayService:
    """Service for controlling Iotzone V5+ Ethernet Relay via HTTP"""

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
        self._lock = threading.Lock()
        self.relay_states = {i: False for i in range(1, 9)}
        self.last_error = None

    def _get_config(self):
        """Get web relay config from settings"""
        from config import config
        return {
            'enabled': config.get('web_relay_enabled', 'false') == 'true',
            'ip': config.get('web_relay_ip', '192.168.1.166'),
            'port': int(config.get('web_relay_port', 80)),
            'username': config.get('web_relay_username', 'admin'),
            'password': config.get('web_relay_password', '12345678'),
            'pulse_time': float(config.get('web_relay_pulse_time', 1.0)),
        }

    def _get_base_url(self, cfg=None):
        """Get base URL for relay"""
        if cfg is None:
            cfg = self._get_config()
        port_str = f":{cfg['port']}" if cfg['port'] != 80 else ""
        return f"http://{cfg['ip']}{port_str}"

    def _make_request(self, endpoint, cfg=None):
        """Make HTTP request to relay board"""
        if cfg is None:
            cfg = self._get_config()

        if not cfg['enabled']:
            logger.debug("Web relay is disabled")
            return None

        url = f"{self._get_base_url(cfg)}/{endpoint}"
        auth = HTTPBasicAuth(cfg['username'], cfg['password'])

        try:
            response = requests.get(url, auth=auth, timeout=5)
            response.raise_for_status()
            self.last_error = None
            return response
        except requests.exceptions.Timeout:
            self.last_error = f"Timeout connecting to {cfg['ip']}"
            logger.error(self.last_error)
        except requests.exceptions.ConnectionError:
            self.last_error = f"Cannot connect to web relay at {cfg['ip']}"
            logger.error(self.last_error)
        except requests.exceptions.HTTPError as e:
            self.last_error = f"HTTP error: {e}"
            logger.error(self.last_error)
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Web relay request error: {e}")
        return None

    def is_enabled(self):
        """Check if web relay is enabled"""
        cfg = self._get_config()
        return cfg['enabled']

    def test_connection(self):
        """Test connection to web relay board"""
        cfg = self._get_config()
        if not cfg['ip']:
            return {'success': False, 'error': 'No IP address configured'}

        try:
            # Try to get the relay status page
            url = f"{self._get_base_url(cfg)}/state.cgi"
            auth = HTTPBasicAuth(cfg['username'], cfg['password'])
            response = requests.get(url, auth=auth, timeout=5)

            if response.status_code == 200:
                return {'success': True, 'message': f"Connected to {cfg['ip']}"}
            elif response.status_code == 401:
                return {'success': False, 'error': 'Authentication failed - check username/password'}
            else:
                return {'success': False, 'error': f"HTTP {response.status_code}"}
        except requests.exceptions.Timeout:
            return {'success': False, 'error': 'Connection timeout'}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'error': f"Cannot connect to {cfg['ip']}"}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def set_relay(self, channel, state):
        """
        Set relay state via HTTP

        Args:
            channel: Relay channel (1-8)
            state: True=ON, False=OFF
        Returns:
            bool: Success status
        """
        if channel < 1 or channel > 8:
            logger.warning(f"Invalid relay channel: {channel}")
            return False

        cfg = self._get_config()
        if not cfg['enabled']:
            return False

        with self._lock:
            # HTTP API: relay.cgi?relayon1=on or relay.cgi?relayoff1=off
            if state:
                endpoint = f"relay.cgi?relayon{channel}=on"
            else:
                endpoint = f"relay.cgi?relayoff{channel}=off"

            response = self._make_request(endpoint, cfg)
            if response:
                self.relay_states[channel] = state
                logger.info(f"Web relay {channel} set to {'ON' if state else 'OFF'}")
                return True
            return False

    def pulse_relay(self, channel, duration=None):
        """
        Pulse relay via HTTP (uses board's built-in pulse)

        Args:
            channel: Relay channel (1-8)
            duration: Not used - pulse time is configured on the board
        Returns:
            bool: Success status
        """
        if channel < 1 or channel > 8:
            logger.warning(f"Invalid relay channel: {channel}")
            return False

        cfg = self._get_config()
        if not cfg['enabled']:
            return False

        # HTTP API: relay.cgi?pulse1=pulse
        endpoint = f"relay.cgi?pulse{channel}=pulse"
        response = self._make_request(endpoint, cfg)
        if response:
            logger.info(f"Web relay {channel} pulsed")
            # Update state temporarily
            self.relay_states[channel] = True
            # Reset after configured pulse time
            def reset_state():
                time.sleep(cfg['pulse_time'])
                self.relay_states[channel] = False
            threading.Thread(target=reset_state, daemon=True).start()
            return True
        return False

    def pulse_multiple(self, channels, duration=None):
        """
        Pulse multiple relays

        Args:
            channels: List of relay channels (1-8)
            duration: Not used for web relay
        Returns:
            bool: Success status
        """
        cfg = self._get_config()
        if not cfg['enabled']:
            return False

        success = True
        for ch in channels:
            if not self.pulse_relay(ch, duration):
                success = False
        return success

    def get_state(self, channel):
        """Get relay state (from cache)"""
        return self.relay_states.get(channel, False)

    def get_all_states(self):
        """Get all relay states"""
        return {
            ch: {
                "name": f"Web Relay {ch}",
                "state": state,
                "pin": None  # No GPIO pin for web relay
            }
            for ch, state in self.relay_states.items()
        }

    def refresh_states(self):
        """Refresh relay states from board (if possible)"""
        cfg = self._get_config()
        if not cfg['enabled']:
            return False

        # Try to get status from state.cgi
        response = self._make_request('state.cgi', cfg)
        if response:
            # Parse the JSON response if available
            try:
                data = response.json()
                # The board returns relay states - parse them
                # Format varies by firmware, this is a best-effort parse
                if 'relay' in data:
                    for i, state in enumerate(data['relay'], 1):
                        if i <= 8:
                            self.relay_states[i] = bool(state)
                return True
            except Exception:
                # If we can't parse, just return True for successful connection
                return True
        return False

    def all_on(self):
        """Turn all relays ON"""
        success = True
        for ch in range(1, 9):
            if not self.set_relay(ch, True):
                success = False
        return success

    def all_off(self):
        """Turn all relays OFF"""
        success = True
        for ch in range(1, 9):
            if not self.set_relay(ch, False):
                success = False
        return success


# Singleton instance
web_relay_service = WebRelayService()