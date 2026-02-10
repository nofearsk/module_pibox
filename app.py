#!/usr/bin/env python3
"""
PiBox Edge Controller
Vehicle Access Control System for Raspberry Pi

Features:
- ANPR camera event receiver
- Local vehicle database (synced from Odoo)
- Automatic barrier control
- WebSocket real-time updates
- Web UI for monitoring
"""
import os
import sys
import logging
import atexit

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_app():
    """Create and configure Flask application"""
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('PIBOX_SECRET_KEY', 'pibox-secret-key')

    # Initialize database
    from database.db import init_db, get_db
    with app.app_context():
        init_db()

    # Register blueprints
    from routes import anpr_bp, api_bp, web_bp
    app.register_blueprint(anpr_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(web_bp)

    # Legacy relay endpoints (for backwards compatibility)
    @app.route('/api/status')
    def legacy_status():
        from services.relay_service import relay_service
        from flask import jsonify
        return jsonify(relay_service.get_all_states())

    @app.route('/api/relay/<int:channel>/<action>', methods=['POST', 'GET'])
    def legacy_relay(channel, action):
        from services.relay_service import relay_service
        from flask import jsonify, request

        if channel < 1 or channel > 8:
            return jsonify({"success": False, "error": "Invalid channel"}), 400

        if action == "on":
            success = relay_service.set_relay(channel, True)
            return jsonify({"success": success, "state": True})
        elif action == "off":
            success = relay_service.set_relay(channel, False)
            return jsonify({"success": success, "state": False})
        elif action == "pulse":
            duration = request.args.get('duration', 1.0, type=float)
            success = relay_service.pulse_relay(channel, duration)
            return jsonify({"success": success, "state": "pulsing", "duration": duration})
        else:
            return jsonify({"success": False, "error": "Invalid action"}), 400

    @app.route('/api/all/<action>', methods=['POST'])
    def legacy_all(action):
        from services.relay_service import relay_service
        from flask import jsonify

        if action == "on":
            relay_service.all_on()
            return jsonify({"success": True, "state": True})
        elif action == "off":
            relay_service.all_off()
            return jsonify({"success": True, "state": False})
        else:
            return jsonify({"success": False, "error": "Invalid action"}), 400

    return app


def start_services():
    """Start background services"""
    from services.relay_service import relay_service
    from services.sync_service import sync_service
    from services.websocket_service import websocket_service
    from config import config

    # Initialize GPIO
    if not relay_service.init_gpio():
        logger.warning("GPIO initialization failed - running in simulation mode")

    # Start WebSocket server
    websocket_service.start(port=8081)

    # Start sync service only if configured
    if config.is_configured:
        sync_service.start_sync_loop()
    else:
        logger.info("Odoo not configured - sync will start after login")

    logger.info("All services started")


def stop_services():
    """Stop background services"""
    from services.relay_service import relay_service
    from services.sync_service import sync_service
    from services.websocket_service import websocket_service

    logger.info("Stopping services...")

    sync_service.stop_sync_loop()
    websocket_service.stop()
    relay_service.cleanup()

    logger.info("All services stopped")


def main():
    """Main entry point"""
    from config import HTTP_PORT, DATA_DIR, IMAGES_DIR

    # Create data directories
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)

    # Create app
    app = create_app()

    # Start services
    start_services()

    # Register cleanup
    atexit.register(stop_services)

    # Run server
    logger.info(f"Starting PiBox Edge Controller on http://0.0.0.0:{HTTP_PORT}")
    logger.info(f"WebSocket server on ws://0.0.0.0:8081")

    try:
        app.run(host='0.0.0.0', port=HTTP_PORT, debug=False, threaded=True)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == '__main__':
    main()
