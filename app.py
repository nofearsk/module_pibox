#!/usr/bin/env python3
"""
RPi Relay Board (B) Web Controller
For Debian 13 Trixie / Raspberry Pi 4 64-bit
Controls 8 relays via web interface with ON, OFF, and PULSE functions
"""

from flask import Flask, render_template, jsonify, request
import lgpio
import threading
import time

app = Flask(__name__)

# BCM pin numbers for 8 relay channels
RELAY_CONFIG = {
    1: {"pin": 5,  "name": "Relay 1", "state": False},
    2: {"pin": 6,  "name": "Relay 2", "state": False},
    3: {"pin": 13, "name": "Relay 3", "state": False},
    4: {"pin": 16, "name": "Relay 4", "state": False},
    5: {"pin": 19, "name": "Relay 5", "state": False},
    6: {"pin": 20, "name": "Relay 6", "state": False},
    7: {"pin": 21, "name": "Relay 7", "state": False},
    8: {"pin": 26, "name": "Relay 8", "state": False},
}

# GPIO handle
gpio_handle = None

def init_gpio():
    """Initialize GPIO pins"""
    global gpio_handle
    try:
        # Try chip 0 first (common), then chip 4 (Pi 5 / some setups)
        for chip in [0, 4]:
            try:
                gpio_handle = lgpio.gpiochip_open(chip)
                print(f"Opened GPIO chip {chip}")
                break
            except:
                continue
        
        if gpio_handle is None:
            raise Exception("Could not open any GPIO chip")
        
        # Setup all pins as output, initially HIGH (relay OFF - active low)
        for channel, config in RELAY_CONFIG.items():
            lgpio.gpio_claim_output(gpio_handle, config["pin"], 1)
            config["state"] = False
        
        print("GPIO initialized successfully")
        return True
    except Exception as e:
        print(f"GPIO init error: {e}")
        return False

def set_relay(channel, state):
    """Set relay state: True=ON, False=OFF"""
    if channel not in RELAY_CONFIG:
        return False
    
    pin = RELAY_CONFIG[channel]["pin"]
    # Active LOW: 0 = ON, 1 = OFF
    lgpio.gpio_write(gpio_handle, pin, 0 if state else 1)
    RELAY_CONFIG[channel]["state"] = state
    return True

def pulse_relay(channel, duration=1.0):
    """Pulse relay ON for specified duration, then OFF"""
    def pulse_thread():
        set_relay(channel, True)
        time.sleep(duration)
        set_relay(channel, False)
    
    thread = threading.Thread(target=pulse_thread)
    thread.start()
    return True

@app.route('/')
def index():
    """Main control page"""
    return render_template('index.html', relays=RELAY_CONFIG)

@app.route('/api/status')
def get_status():
    """Get all relay states"""
    status = {}
    for channel, config in RELAY_CONFIG.items():
        status[channel] = {
            "name": config["name"],
            "state": config["state"]
        }
    return jsonify(status)

@app.route('/api/relay/<int:channel>/<action>', methods=['POST','GET'])
def control_relay(channel, action):
    """Control a specific relay"""
    if channel not in RELAY_CONFIG:
        return jsonify({"success": False, "error": "Invalid channel"}), 400
    
    if action == "on":
        success = set_relay(channel, True)
        return jsonify({"success": success, "state": True})
    
    elif action == "off":
        success = set_relay(channel, False)
        return jsonify({"success": success, "state": False})
    
    elif action == "pulse":
        duration = request.args.get('duration', 1.0, type=float)
        success = pulse_relay(channel, duration)
        return jsonify({"success": success, "state": "pulsing", "duration": duration})
    
    else:
        return jsonify({"success": False, "error": "Invalid action"}), 400

@app.route('/api/all/<action>', methods=['POST'])
def control_all(action):
    """Control all relays at once"""
    if action == "on":
        for channel in RELAY_CONFIG:
            set_relay(channel, True)
        return jsonify({"success": True, "state": True})
    
    elif action == "off":
        for channel in RELAY_CONFIG:
            set_relay(channel, False)
        return jsonify({"success": True, "state": False})
    
    else:
        return jsonify({"success": False, "error": "Invalid action"}), 400

def cleanup():
    """Cleanup GPIO on exit"""
    global gpio_handle
    if gpio_handle:
        for config in RELAY_CONFIG.values():
            lgpio.gpio_write(gpio_handle, config["pin"], 1)  # All OFF
        lgpio.gpiochip_close(gpio_handle)
        print("GPIO cleanup complete")

if __name__ == '__main__':
    import atexit
    
    if init_gpio():
        atexit.register(cleanup)
        print("Starting Relay Web Controller on http://0.0.0.0:8080")
        app.run(host='0.0.0.0', port=8080, debug=False)
    else:
        print("Failed to initialize GPIO. Are you running as root?")
        print("Try: sudo python3 app.py")
