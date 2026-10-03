"""
Relay Control Service
Facade + per-channel router for barrier relay control.

Each PiBox relay channel can be routed - independently - to one of three
backends, chosen by the grid row's 'brand':
  - hikvision : a Hikvision camera barrier (ISAPI)
  - web       : a channel on the Web Relay board
  - gpio      : a Raspberry Pi GPIO relay pin

Each grid row has its own 'On' switch. A channel with no enabled, configured
grid row falls back to the legacy backend (Web Relay if configured,
otherwise GPIO).
"""
import json
import threading
import time
import logging

logger = logging.getLogger(__name__)

# Try to import lgpio, but allow running without it for development
try:
    import lgpio
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    logger.warning("lgpio not available - running in simulation mode")


class RelayService:
    """Routes barrier control to the backend configured per channel."""

    _instance = None

    # BCM pin numbers for 8 relay channels
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

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.gpio_handle = None
        self.relay_states = {i: False for i in range(1, 9)}
        self.relay_names = {i: f"Relay {i}" for i in range(1, 9)}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ GPIO setup
    def init_gpio(self):
        """Initialize GPIO pins"""
        if not GPIO_AVAILABLE:
            logger.info("GPIO simulation mode - no hardware control")
            return True

        try:
            # Try chip 0 first (common), then chip 4 (Pi 5)
            for chip in [0, 4]:
                try:
                    self.gpio_handle = lgpio.gpiochip_open(chip)
                    logger.info(f"Opened GPIO chip {chip}")
                    break
                except Exception:
                    continue

            if self.gpio_handle is None:
                raise Exception("Could not open any GPIO chip")

            # Setup all pins as output, initially HIGH (relay OFF - active low)
            for channel, pin in self.RELAY_PINS.items():
                lgpio.gpio_claim_output(self.gpio_handle, pin, 1)
                self.relay_states[channel] = False

            logger.info("GPIO initialized successfully")
            return True
        except Exception as e:
            logger.error(f"GPIO init error: {e}")
            return False

    def cleanup(self):
        """Cleanup GPIO on exit"""
        if self.gpio_handle and GPIO_AVAILABLE:
            for pin in self.RELAY_PINS.values():
                lgpio.gpio_write(self.gpio_handle, pin, 1)  # All OFF
            lgpio.gpiochip_close(self.gpio_handle)
            logger.info("GPIO cleanup complete")

    # -------------------------------------------------------------- routing
    def _web_fallback_enabled(self):
        """True when the Web Relay board is the legacy fallback backend."""
        try:
            from config import config
            return config.get('web_relay_enabled', 'false') == 'true'
        except Exception:
            return False

    def _rows_for_channel(self, channel):
        """Enabled grid rows mapped to a PiBox relay channel.

        A row's own 'On' checkbox is the only switch. Rows that are disabled,
        or Hikvision rows with no IP, are skipped so the channel falls back
        to the legacy backend.
        """
        try:
            from config import config
            rows = json.loads(config.get('camera_relay_devices', '') or '[]')
            if not isinstance(rows, list):
                return []
        except Exception:
            return []
        out = []
        for r in rows:
            if not r:
                continue
            if r.get('enabled', True) is False or r.get('enabled') == 'false':
                continue
            brand = (r.get('brand') or 'hikvision').lower()
            if brand == 'hikvision' and not (r.get('ip') or '').strip():
                continue  # not configured yet - fall back to legacy
            try:
                if int(r.get('pi_map')) == int(channel):
                    out.append(r)
            except (TypeError, ValueError):
                continue
        return out

    def _dispatch(self, row, action, duration=1.0, state=True):
        """Send one action to the backend named by a grid row's brand."""
        brand = (row.get('brand') or 'hikvision').lower()
        target = row.get('target') or row.get('trigger') or row.get('pi_map')

        if brand == 'hikvision':
            from services.camera_relay_service import camera_relay_service
            device = dict(row)
            device['trigger'] = target
            if action == 'pulse':
                return camera_relay_service.trigger_device(device).get('success', False)
            return camera_relay_service.set_device(device, state)

        if brand == 'web':
            from services.web_relay_service import web_relay_service
            try:
                ch = int(target)
            except (TypeError, ValueError):
                logger.warning(f"Web relay row has invalid target: {target}")
                return False
            if action == 'pulse':
                return web_relay_service.pulse_relay(ch, duration)
            return web_relay_service.set_relay(ch, state)

        if brand == 'gpio':
            try:
                ch = int(target)
            except (TypeError, ValueError):
                logger.warning(f"GPIO row has invalid target: {target}")
                return False
            if action == 'pulse':
                return self._gpio_pulse(ch, duration)
            return self._gpio_set(ch, state)

        logger.warning(f"Unknown relay brand: {brand}")
        return False

    # ------------------------------------------------------- legacy backend
    def _legacy_pulse(self, channel, duration):
        if self._web_fallback_enabled():
            from services.web_relay_service import web_relay_service
            return web_relay_service.pulse_relay(channel, duration)
        return self._gpio_pulse(channel, duration)

    def _legacy_set(self, channel, state):
        if self._web_fallback_enabled():
            from services.web_relay_service import web_relay_service
            return web_relay_service.set_relay(channel, state)
        return self._gpio_set(channel, state)

    # ----------------------------------------------------------- raw GPIO
    def _gpio_pulse(self, channel, duration):
        """Pulse a GPIO relay ON for duration, then OFF (background thread)."""
        if channel not in self.RELAY_PINS:
            logger.warning(f"Invalid GPIO relay channel: {channel}")
            return False

        def pulse_thread():
            with self._lock:
                if GPIO_AVAILABLE and self.gpio_handle:
                    lgpio.gpio_write(self.gpio_handle, self.RELAY_PINS[channel], 0)
                self.relay_states[channel] = True
            logger.info(f"GPIO relay {channel} ON")
            time.sleep(duration)
            with self._lock:
                if GPIO_AVAILABLE and self.gpio_handle:
                    lgpio.gpio_write(self.gpio_handle, self.RELAY_PINS[channel], 1)
                self.relay_states[channel] = False
            logger.info(f"GPIO relay {channel} OFF")

        threading.Thread(target=pulse_thread, daemon=True).start()
        logger.info(f"GPIO relay {channel} pulsing for {duration}s")
        return True

    def _gpio_set(self, channel, state):
        """Set a GPIO relay steady ON or OFF."""
        if channel not in self.RELAY_PINS:
            logger.warning(f"Invalid GPIO relay channel: {channel}")
            return False
        with self._lock:
            if GPIO_AVAILABLE and self.gpio_handle:
                # Active LOW: 0 = ON, 1 = OFF
                lgpio.gpio_write(self.gpio_handle, self.RELAY_PINS[channel],
                                 0 if state else 1)
            self.relay_states[channel] = state
        logger.info(f"GPIO relay {channel} set to {'ON' if state else 'OFF'}")
        return True

    # ------------------------------------------------------------ public API
    def set_relay(self, channel, state):
        """Set a relay channel steady ON or OFF, routed per the grid."""
        rows = self._rows_for_channel(channel)
        if rows:
            results = [self._dispatch(r, 'set', state=state) for r in rows]
            self.relay_states[channel] = state
            return all(results)
        return self._legacy_set(channel, state)

    def pulse_relay(self, channel, duration=1.0):
        """Pulse a relay channel, routed per the grid."""
        rows = self._rows_for_channel(channel)
        if rows:
            results = [self._dispatch(r, 'pulse', duration=duration) for r in rows]
            self.relay_states[channel] = True

            def reset_state():
                time.sleep(duration)
                self.relay_states[channel] = False
            threading.Thread(target=reset_state, daemon=True).start()
            logger.info(f"Relay channel {channel} pulsed via {len(rows)} mapping(s)")
            return all(results)
        return self._legacy_pulse(channel, duration)

    def pulse_multiple(self, channels, duration=1.0):
        """Pulse several relay channels (each routed independently)."""
        ok = True
        for ch in channels:
            if not self.pulse_relay(ch, duration):
                ok = False
        return ok

    def get_state(self, channel):
        """Get relay state"""
        return self.relay_states.get(channel, False)

    def get_all_states(self):
        """Get all relay states, labelled with their routing."""
        states = {}
        for ch in range(1, 9):
            rows = self._rows_for_channel(ch)
            if rows:
                parts = []
                for r in rows:
                    brand = (r.get('brand') or 'hikvision').lower()
                    tgt = r.get('target') or r.get('trigger') or r.get('pi_map')
                    if brand == 'hikvision':
                        parts.append(f"Cam {r.get('ip', '')}")
                    elif brand == 'web':
                        parts.append(f"Web ch{tgt}")
                    else:
                        parts.append(f"GPIO ch{tgt}")
                name = f"{self.relay_names.get(ch, f'Relay {ch}')} → " + ", ".join(parts)
            else:
                name = self.relay_names.get(ch, f"Relay {ch}")
            states[ch] = {
                "name": name,
                "state": self.relay_states.get(ch, False),
                "pin": self.RELAY_PINS.get(ch),
            }
        return states

    def get_mode(self):
        """Get current relay mode: 'mapped', 'web' or 'gpio'."""
        for ch in range(1, 9):
            if self._rows_for_channel(ch):
                return 'mapped'
        return 'web' if self._web_fallback_enabled() else 'gpio'

    def set_relay_name(self, channel, name):
        """Set custom name for relay"""
        if channel in self.relay_names:
            self.relay_names[channel] = name

    def all_on(self):
        """Turn all relays ON"""
        for ch in self.RELAY_PINS:
            self.set_relay(ch, True)

    def all_off(self):
        """Turn all relays OFF"""
        for ch in self.RELAY_PINS:
            self.set_relay(ch, False)


# Singleton instance
relay_service = RelayService()
