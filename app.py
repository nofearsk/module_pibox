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

from flask import Flask, session
from datetime import timedelta

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

    # Session configuration
    from config import config
    session_timeout = int(config.get('session_timeout', 30))  # Default 30 minutes
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=session_timeout)

    # Initialize database
    from database.db import init_db, get_db
    with app.app_context():
        init_db()

    # Session timeout handler
    @app.before_request
    def check_session_timeout():
        from flask import request
        from datetime import datetime

        # Skip for static files and login pages
        if request.endpoint in ['static', 'web.admin_login', 'web.admin_setup', 'web.admin_logout']:
            return

        # Make session permanent to use PERMANENT_SESSION_LIFETIME
        session.permanent = True

        # Check last activity
        last_activity = session.get('last_activity')
        if last_activity:
            last_time = datetime.fromisoformat(last_activity)
            timeout_minutes = int(config.get('session_timeout', 30))
            if (datetime.now() - last_time).total_seconds() > timeout_minutes * 60:
                session.clear()
                return

        # Update last activity
        if session.get('admin_logged_in'):
            session['last_activity'] = datetime.now().isoformat()

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
