"""
Relay/GPIO Control Service
Controls 8-channel relay board via GPIO or Web Relay (Iotzone V5+)

Supports two modes:
- GPIO: Direct control via Raspberry Pi GPIO pins
- Web Relay: HTTP control of Iotzone V5+ 8-Channel Ethernet Relay
"""
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
    """Service for controlling GPIO relays"""

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
        self._web_relay = None

    def _get_web_relay(self):
        """Lazy load web relay service"""
        if self._web_relay is None:
            from services.web_relay_service import web_relay_service
            self._web_relay = web_relay_service
        return self._web_relay

    def _use_web_relay(self):
        """Check if web relay should be used"""
        try:
            from config import config
            return config.get('web_relay_enabled', 'false') == 'true'
        except Exception:
            return False

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

    def set_relay(self, channel, state):
        """
        Set relay state
        Args:
            channel: Relay channel (1-8)
            state: True=ON, False=OFF
        Returns:
            bool: Success status
        """
        # Check if web relay is enabled
        if self._use_web_relay():
            return self._get_web_relay().set_relay(channel, state)

        if channel not in self.RELAY_PINS:
            logger.warning(f"Invalid relay channel: {channel}")
            return False

        with self._lock:
            if GPIO_AVAILABLE and self.gpio_handle:
                pin = self.RELAY_PINS[channel]
                # Active LOW: 0 = ON, 1 = OFF
                lgpio.gpio_write(self.gpio_handle, pin, 0 if state else 1)

            self.relay_states[channel] = state
            logger.info(f"Relay {channel} set to {'ON' if state else 'OFF'}")
            return True

    def pulse_relay(self, channel, duration=1.0):
        """
        Pulse relay ON for specified duration, then OFF
        Args:
            channel: Relay channel (1-8)
            duration: Pulse duration in seconds
        Returns:
            bool: Success status
        """
        # Check if web relay is enabled
        if self._use_web_relay():
            return self._get_web_relay().pulse_relay(channel, duration)

        if channel not in self.RELAY_PINS:
            return False

        def pulse_thread():
            # Use GPIO directly for pulse to avoid recursion
            with self._lock:
                if GPIO_AVAILABLE and self.gpio_handle:
                    pin = self.RELAY_PINS[channel]
                    lgpio.gpio_write(self.gpio_handle, pin, 0)  # ON
                self.relay_states[channel] = True
            logger.info(f"Relay {channel} ON")

            time.sleep(duration)

            with self._lock:
                if GPIO_AVAILABLE and self.gpio_handle:
                    pin = self.RELAY_PINS[channel]
                    lgpio.gpio_write(self.gpio_handle, pin, 1)  # OFF
                self.relay_states[channel] = False
            logger.info(f"Relay {channel} OFF")

        thread = threading.Thread(target=pulse_thread, daemon=True)
        thread.start()
        logger.info(f"Relay {channel} pulsing for {duration}s")
        return True

    def pulse_multiple(self, channels, duration=1.0):
        """
        Pulse multiple relays simultaneously
        Args:
            channels: List of relay channels
            duration: Pulse duration in seconds
        """
        # Check if web relay is enabled
        if self._use_web_relay():
            return self._get_web_relay().pulse_multiple(channels, duration)

        def pulse_thread():
            # Turn all ON
            for ch in channels:
                with self._lock:
                    if GPIO_AVAILABLE and self.gpio_handle and ch in self.RELAY_PINS:
                        pin = self.RELAY_PINS[ch]
                        lgpio.gpio_write(self.gpio_handle, pin, 0)
                    self.relay_states[ch] = True

            time.sleep(duration)

            # Turn all OFF
            for ch in channels:
                with self._lock:
                    if GPIO_AVAILABLE and self.gpio_handle and ch in self.RELAY_PINS:
                        pin = self.RELAY_PINS[ch]
                        lgpio.gpio_write(self.gpio_handle, pin, 1)
                    self.relay_states[ch] = False

        thread = threading.Thread(target=pulse_thread, daemon=True)
        thread.start()
        logger.info(f"Relays {channels} pulsing for {duration}s")
        return True

    def get_state(self, channel):
        """Get relay state"""
        if self._use_web_relay():
            return self._get_web_relay().get_state(channel)
        return self.relay_states.get(channel, False)

    def get_all_states(self):
        """Get all relay states"""
        if self._use_web_relay():
            web_states = self._get_web_relay().get_all_states()
            # Add custom names
            for ch in web_states:
                web_states[ch]["name"] = self.relay_names.get(ch, f"Web Relay {ch}")
            return web_states

        return {
            ch: {
                "name": self.relay_names[ch],
                "state": state,
                "pin": self.RELAY_PINS[ch]
            }
            for ch, state in self.relay_states.items()
        }

    def get_mode(self):
        """Get current relay mode"""
        return 'web' if self._use_web_relay() else 'gpio'

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
