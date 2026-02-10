"""
Relay/GPIO Control Service
Controls 8-channel relay board via GPIO
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
        if channel not in self.RELAY_PINS:
            return False

        def pulse_thread():
            self.set_relay(channel, True)
            time.sleep(duration)
            self.set_relay(channel, False)

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
        def pulse_thread():
            for ch in channels:
                self.set_relay(ch, True)
            time.sleep(duration)
            for ch in channels:
                self.set_relay(ch, False)

        thread = threading.Thread(target=pulse_thread, daemon=True)
        thread.start()
        logger.info(f"Relays {channels} pulsing for {duration}s")
        return True

    def get_state(self, channel):
        """Get relay state"""
        return self.relay_states.get(channel, False)

    def get_all_states(self):
        """Get all relay states"""
        return {
            ch: {
                "name": self.relay_names[ch],
                "state": state,
                "pin": self.RELAY_PINS[ch]
            }
            for ch, state in self.relay_states.items()
        }

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
