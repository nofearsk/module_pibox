"""
Camera Relay Service
Controls barriers via Hikvision devices using the ISAPI HTTP API.

Supports a grid of up to 8 camera relay devices. Each device row carries its
own connection details and is bound to a PiBox relay channel (1-8) via its
'pi_map' field. When the app pulses relay channel N, every device whose
pi_map == N fires.

Per-device 'type':
- 'parking'  : ANPR / entrance camera barrier -> PUT /ISAPI/Parking/channels/<id>/barrierGate
- 'alarmout' : camera / NVR alarm output      -> PUT /ISAPI/System/IO/outputs/<id>/trigger
- 'door'     : access controller door         -> PUT /ISAPI/AccessControl/RemoteControl/door/<id>

Auth: HTTP Digest (Hikvision default), with a Basic-auth fallback.

This service exposes the same method surface as web_relay_service so that
relay_service can delegate to it transparently.
"""
import json
import re
import threading
import time
import logging
import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth

logger = logging.getLogger(__name__)

mode_name = 'camera'

# ISAPI XML payloads
_NS = 'http://www.hikvision.com/ver20/XMLSchema'
_OUTPUT_BODY = (
    '<IOPortData version="2.0" xmlns="%s">'
    '<outputState>%s</outputState>'
    '</IOPortData>'
)
_DOOR_BODY = (
    '<RemoteControlDoor version="2.0" xmlns="%s">'
    '<cmd>%s</cmd>'
    '</RemoteControlDoor>'
)
_BARRIER_BODY = (
    '<BarrierGate version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
    '<ctrlMode>open</ctrlMode>'
    '</BarrierGate>'
)


class CameraRelayService:
    """Controls barriers through a grid of Hikvision devices via ISAPI."""

    _instance = None
    mode_name = 'camera'

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

    # ------------------------------------------------------------------ config
    def _get_config(self):
        """Read camera relay config (master settings + device grid)."""
        from config import config
        try:
            devices = json.loads(config.get('camera_relay_devices', '') or '[]')
            if not isinstance(devices, list):
                devices = []
        except Exception:
            devices = []
        return {
            'enabled': config.get('camera_relay_enabled', 'false') == 'true',
            'pulse_time': float(config.get('camera_relay_pulse_time', 1.0) or 1.0),
            'invert': config.get('camera_relay_invert', 'false') == 'true',
            'devices': devices,
        }

    def _devices_for_channel(self, channel, cfg):
        """Return enabled devices bound to a PiBox relay channel."""
        out = []
        for d in cfg['devices']:
            if not d or not d.get('ip'):
                continue
            # A row is active unless explicitly disabled (default: enabled)
            if d.get('enabled', True) is False or d.get('enabled') == 'false':
                continue
            try:
                if int(d.get('pi_map')) == int(channel):
                    out.append(d)
            except (TypeError, ValueError):
                continue
        return out

    # ----------------------------------------------------------------- request
    def _request(self, method, path, device, body=None):
        """Send an ISAPI request to one device, trying Digest then Basic auth."""
        ip = (device.get('ip') or '').strip()
        if not ip:
            self.last_error = 'No IP address'
            return None
        try:
            port = int(device.get('port') or 80)
        except (TypeError, ValueError):
            port = 80
        user = device.get('username') or 'admin'
        pw = device.get('password') or ''
        port_str = f":{port}" if port != 80 else ""
        url = f"http://{ip}{port_str}{path}"
        headers = {'Content-Type': 'application/xml'}

        for auth in (HTTPDigestAuth(user, pw), HTTPBasicAuth(user, pw)):
            try:
                resp = requests.request(method, url, auth=auth, data=body,
                                        headers=headers, timeout=5)
                if resp.status_code == 401:
                    continue  # wrong auth scheme - try the next
                resp.raise_for_status()
                self.last_error = None
                return resp
            except requests.exceptions.Timeout:
                self.last_error = f"Timeout connecting to {ip}"
                logger.error(self.last_error)
                return None
            except requests.exceptions.ConnectionError:
                self.last_error = f"Cannot connect to camera at {ip}"
                logger.error(self.last_error)
                return None
            except requests.exceptions.HTTPError as e:
                self.last_error = f"HTTP error from {ip}: {e}"
                logger.error(self.last_error)
                return None
            except Exception as e:
                self.last_error = f"{ip}: {e}"
                logger.error(f"Camera relay request error: {e}")
                return None
        self.last_error = f"Authentication failed for {ip} - check username/password"
        logger.error(self.last_error)
        return None

    # ------------------------------------------------------------- public API
    def is_enabled(self):
        return self._get_config()['enabled']

    def test_device(self, device):
        """Verify a single device (grid row) is reachable with valid creds."""
        if not device or not (device.get('ip') or '').strip():
            return {'success': False, 'error': 'No IP address configured'}
        resp = self._request('GET', '/ISAPI/System/deviceInfo', device)
        if resp is not None:
            name = ''
            try:
                m = re.search(r'<deviceName>(.*?)</deviceName>', resp.text)
                if m:
                    name = m.group(1)
            except Exception:
                pass
            msg = f"Connected to {device.get('ip')}"
            if name:
                msg += f" ({name})"
            return {'success': True, 'message': msg}
        return {'success': False, 'error': self.last_error or 'Connection failed'}

    def trigger_device(self, device):
        """Fire one device immediately and open the barrier.

        Ad-hoc test action - works regardless of the master enable toggle.
        """
        if not device or not (device.get('ip') or '').strip():
            return {'success': False, 'error': 'No IP address configured'}
        cfg = self._get_config()
        if not self._fire_device(device, True, cfg):
            return {'success': False, 'error': self.last_error or 'Trigger failed'}
        # Alarm-output devices need an explicit release after the pulse window
        if (device.get('type') or 'parking').lower() == 'alarmout':
            def release():
                time.sleep(cfg['pulse_time'])
                self._fire_device(device, False, cfg)
            threading.Thread(target=release, daemon=True).start()
        logger.info(f"Camera relay manually triggered: {device.get('ip')}")
        return {'success': True,
                'message': f"Triggered {device.get('ip')} - barrier should open"}

    def set_device(self, device, state):
        """Set one device's barrier/relay steady ON/OFF.

        Used by the relay router for grid-mapped Hikvision channels.
        """
        if not device or not (device.get('ip') or '').strip():
            return False
        cfg = self._get_config()
        return self._fire_device(device, bool(state), cfg)

    def test_connection(self):
        """Test every configured device. Returns a per-device summary."""
        cfg = self._get_config()
        results = []
        ok = True
        for d in cfg['devices']:
            if not d or not d.get('ip'):
                continue
            if (d.get('brand') or 'hikvision').lower() != 'hikvision':
                continue  # web / gpio rows have nothing to connection-test
            r = self.test_device(d)
            results.append({'ip': d.get('ip'), 'pi_map': d.get('pi_map'), **r})
            if not r.get('success'):
                ok = False
        if not results:
            return {'success': False, 'error': 'No camera devices configured'}
        return {'success': ok, 'results': results}

    def _fire_device(self, device, state, cfg):
        """Drive one device. state=True triggers, state=False releases.

        Parking-barrier and door devices have no release - the gate manages
        its own close cycle, so state=False is a no-op for them.
        """
        dev_type = (device.get('type') or 'parking').lower()
        target_id = str(device.get('trigger') or '1')

        if dev_type == 'parking':
            # Hikvision ANPR / entrance camera barrier gate
            if not state:
                return True
            path = f"/ISAPI/Parking/channels/{target_id}/barrierGate"
            return self._request('PUT', path, device, _BARRIER_BODY) is not None

        if dev_type == 'door':
            if not state:
                return True
            body = _DOOR_BODY % (_NS, 'open')
            path = f"/ISAPI/AccessControl/RemoteControl/door/{target_id}"
            return self._request('PUT', path, device, body) is not None

        # alarm-output mode
        active = 'low' if cfg['invert'] else 'high'
        idle = 'high' if cfg['invert'] else 'low'
        level = active if state else idle
        body = _OUTPUT_BODY % (_NS, level)
        path = f"/ISAPI/System/IO/outputs/{target_id}/trigger"
        return self._request('PUT', path, device, body) is not None

    def set_relay(self, channel, state):
        """Set every device on a channel steady ON or OFF."""
        if channel < 1 or channel > 8:
            logger.warning(f"Invalid relay channel: {channel}")
            return False
        cfg = self._get_config()
        if not cfg['enabled']:
            return False

        devices = self._devices_for_channel(channel, cfg)
        if not devices:
            logger.warning(f"Camera relay: no device mapped to channel {channel}")
            return False

        ok = True
        with self._lock:
            for d in devices:
                if not self._fire_device(d, state, cfg):
                    ok = False
            self.relay_states[channel] = state
        logger.info(f"Camera relay channel {channel} set to "
                    f"{'ON' if state else 'OFF'} ({len(devices)} device(s))")
        return ok

    def pulse_relay(self, channel, duration=None):
        """Pulse every device mapped to a channel: trigger, wait, release."""
        if channel < 1 or channel > 8:
            logger.warning(f"Invalid relay channel: {channel}")
            return False
        cfg = self._get_config()
        if not cfg['enabled']:
            return False

        devices = self._devices_for_channel(channel, cfg)
        if not devices:
            logger.warning(f"Camera relay: no device mapped to channel {channel}")
            return False
        pulse_time = float(duration) if duration else cfg['pulse_time']

        # Trigger all devices on this channel
        ok = True
        for d in devices:
            if not self._fire_device(d, True, cfg):
                ok = False
        self.relay_states[channel] = True
        logger.info(f"Camera relay channel {channel} pulsing for {pulse_time}s "
                    f"({len(devices)} device(s))")

        # Release after the pulse window (alarm outputs only; door is a no-op)
        def release_thread():
            time.sleep(pulse_time)
            with self._lock:
                for d in devices:
                    self._fire_device(d, False, cfg)
                self.relay_states[channel] = False
            logger.info(f"Camera relay channel {channel} released")
        threading.Thread(target=release_thread, daemon=True).start()
        return ok

    def pulse_multiple(self, channels, duration=None):
        """Pulse several channels."""
        cfg = self._get_config()
        if not cfg['enabled']:
            return False
        success = True
        for ch in channels:
            if not self.pulse_relay(ch, duration):
                success = False
        return success

    def get_state(self, channel):
        return self.relay_states.get(channel, False)

    def get_all_states(self):
        """Return channel states, labelled with the bound device IP if any."""
        cfg = self._get_config()
        states = {}
        for ch in range(1, 9):
            devices = self._devices_for_channel(ch, cfg)
            if devices:
                label = ", ".join(d.get('ip', '') for d in devices)
                name = f"Camera Relay {ch} ({label})"
            else:
                name = f"Camera Relay {ch}"
            states[ch] = {
                "name": name,
                "state": self.relay_states.get(ch, False),
                "pin": None,
            }
        return states

    def refresh_states(self):
        """No reliable steady-state readback for pulsed outputs - no-op."""
        return self._get_config()['enabled']

    def all_on(self):
        success = True
        for ch in range(1, 9):
            if self._devices_for_channel(ch, self._get_config()):
                if not self.set_relay(ch, True):
                    success = False
        return success

    def all_off(self):
        success = True
        for ch in range(1, 9):
            if self._devices_for_channel(ch, self._get_config()):
                if not self.set_relay(ch, False):
                    success = False
        return success


# Singleton instance
camera_relay_service = CameraRelayService()
