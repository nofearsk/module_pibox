"""
REST API Routes
Local management and data access endpoints
"""
from flask import request, jsonify, send_from_directory
import os
import logging

from . import api_bp
from database.models import VehicleModel, BarrierModel, AccessLogModel, LocationModel, AnprCameraModel
from services.relay_service import relay_service
from services.sync_service import sync_service
from services.websocket_service import websocket_service
from services.odoo_api import odoo_api, OdooAPIError
from config import config, IMAGES_DIR

logger = logging.getLogger(__name__)


# ============== Authentication ==============

@api_bp.route('/api/auth/login', methods=['POST'])
def auth_login():
    """Login to Odoo and get token"""
    try:
        data = request.get_json()

        odoo_url = data.get('odoo_url', '').strip()
        username = data.get('username', '').strip()
        password = data.get('password', '')

        if not odoo_url:
            return jsonify({'success': False, 'error': 'Odoo URL is required'}), 400
        if not username:
            return jsonify({'success': False, 'error': 'Username is required'}), 400
        if not password:
            return jsonify({'success': False, 'error': 'Password is required'}), 400

        # Attempt login
        result = odoo_api.login(odoo_url, username, password)

        # Reload config to get the new token
        config.clear_cache()

        # Start sync after successful login
        sync_service.start_sync_loop()

        return jsonify({
            'success': True,
            'message': 'Login successful',
            'username': result['username']
        })

    except OdooAPIError as e:
        return jsonify({'success': False, 'error': str(e)}), 401
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    """Logout and clear token"""
    try:
        odoo_api.logout()
        config.clear_cache()
        return jsonify({'success': True, 'message': 'Logged out'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/auth/status', methods=['GET'])
def auth_status():
    """Get authentication status"""
    try:
        status = odoo_api.get_status()
        return jsonify({'success': True, **status})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/auth/test', methods=['GET'])
def auth_test():
    """Test Odoo connection"""
    try:
        success, message = odoo_api.test_connection()
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============== Vehicles ==============

@api_bp.route('/api/vehicles', methods=['GET'])
def list_vehicles():
    """List all vehicles"""
    try:
        vehicles = VehicleModel.get_all()
        return jsonify({
            'success': True,
            'vehicles': [dict(v) for v in vehicles],
            'total': len(vehicles)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/vehicles/search', methods=['GET'])
def search_vehicles():
    """Search vehicles by plate"""
    query = request.args.get('plate', '')
    limit = request.args.get('limit', 50, type=int)

    try:
        vehicles = VehicleModel.search(query, limit)
        return jsonify({
            'success': True,
            'vehicles': [dict(v) for v in vehicles],
            'total': len(vehicles)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/vehicles/<plate>', methods=['GET'])
def get_vehicle(plate):
    """Get vehicle by plate"""
    try:
        vehicle = VehicleModel.get_by_plate(plate)
        if vehicle:
            return jsonify({
                'success': True,
                'vehicle': dict(vehicle),
                'valid': VehicleModel.is_valid(vehicle)
            })
        return jsonify({'success': False, 'error': 'Vehicle not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============== Locations ==============

@api_bp.route('/api/locations', methods=['GET'])
def list_locations():
    """List all locations (synced from Odoo)"""
    try:
        locations = LocationModel.get_all()
        return jsonify({
            'success': True,
            'locations': [dict(loc) for loc in locations],
            'total': len(locations)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/locations/<int:location_id>', methods=['GET'])
def get_location(location_id):
    """Get location by Odoo ID"""
    try:
        location = LocationModel.get_by_odoo_id(location_id)
        if location:
            return jsonify({
                'success': True,
                'location': dict(location)
            })
        return jsonify({'success': False, 'error': 'Location not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============== ANPR Cameras ==============

@api_bp.route('/api/anpr-cameras', methods=['GET'])
def list_anpr_cameras():
    """List all ANPR cameras (synced from Odoo)"""
    try:
        cameras = AnprCameraModel.get_all()
        return jsonify({
            'success': True,
            'cameras': [dict(cam) for cam in cameras],
            'total': len(cameras)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/anpr-cameras/<reg_code>', methods=['GET'])
def get_anpr_camera(reg_code):
    """Get ANPR camera by registration code"""
    try:
        camera = AnprCameraModel.get_by_reg_code(reg_code)
        if camera:
            # Also get the location info
            location = LocationModel.get_by_odoo_id(camera['location_id']) if camera['location_id'] else None
            return jsonify({
                'success': True,
                'camera': dict(camera),
                'location': dict(location) if location else None
            })
        return jsonify({'success': False, 'error': 'Camera not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/anpr-cameras/<reg_code>/relay', methods=['GET', 'POST', 'PUT'])
def anpr_camera_relay(reg_code):
    """Get or set relay channels for an ANPR camera"""
    try:
        camera = AnprCameraModel.get_by_reg_code(reg_code)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        if request.method == 'GET':
            relay_channels = AnprCameraModel.get_relay_channels(reg_code)
            return jsonify({
                'success': True,
                'reg_code': reg_code,
                'camera_name': camera['name'],
                'relay_channels': relay_channels
            })
        else:
            # POST/PUT - set relay channels
            data = request.get_json()
            relay_channels = data.get('relay_channels', [])

            # Validate relay channels
            if not isinstance(relay_channels, list):
                relay_channels = [relay_channels]
            relay_channels = [int(ch) for ch in relay_channels if ch]

            AnprCameraModel.set_relay_channels_by_reg_code(reg_code, relay_channels)

            return jsonify({
                'success': True,
                'reg_code': reg_code,
                'camera_name': camera['name'],
                'relay_channels': relay_channels,
                'message': 'Relay channels updated'
            })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============== Access Logs ==============

@api_bp.route('/api/access-logs', methods=['GET'])
def list_access_logs():
    """List recent access logs"""
    limit = request.args.get('limit', 50, type=int)
    vehicle_type = request.args.get('type')

    try:
        logs = AccessLogModel.get_recent(limit, vehicle_type)
        return jsonify({
            'success': True,
            'logs': [dict(log) for log in logs],
            'total': len(logs)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/access-logs/stats', methods=['GET'])
def get_access_stats():
    """Get today's access statistics"""
    try:
        stats = AccessLogModel.get_today_stats()
        return jsonify({
            'success': True,
            'stats': stats
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============== Barriers ==============

@api_bp.route('/api/barriers', methods=['GET'])
def list_barriers():
    """List barrier mappings"""
    try:
        barriers = BarrierModel.get_all()
        return jsonify({
            'success': True,
            'barriers': [dict(b) for b in barriers]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/barriers', methods=['POST'])
def create_barrier():
    """Create barrier mapping"""
    try:
        data = request.get_json()
        barrier_id = BarrierModel.create(
            camera_ip=data['camera_ip'],
            relay_channels=data['relay_channels'],
            camera_name=data.get('camera_name'),
            direction=data.get('direction', 'both'),
            location_name=data.get('location_name'),
            location_id=data.get('location_id')  # Odoo location ID
        )
        return jsonify({'success': True, 'id': barrier_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/barriers/<int:barrier_id>', methods=['PUT'])
def update_barrier(barrier_id):
    """Update barrier mapping"""
    try:
        data = request.get_json()
        BarrierModel.update(barrier_id, **data)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/barriers/<int:barrier_id>', methods=['DELETE'])
def delete_barrier(barrier_id):
    """Delete barrier mapping"""
    try:
        BarrierModel.delete(barrier_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============== Relay Control ==============

@api_bp.route('/api/relay/status', methods=['GET'])
def get_relay_status():
    """Get all relay states"""
    return jsonify({
        'success': True,
        'mode': relay_service.get_mode(),
        'relays': relay_service.get_all_states()
    })


# ============== Web Relay (Iotzone V5+) ==============

@api_bp.route('/api/web-relay/test', methods=['POST'])
def test_web_relay():
    """Test connection to Iotzone V5+ Ethernet Relay"""
    try:
        from services.web_relay_service import web_relay_service
        result = web_relay_service.test_connection()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Web relay test error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/web-relay/status', methods=['GET'])
def get_web_relay_status():
    """Get web relay status"""
    try:
        from services.web_relay_service import web_relay_service
        cfg = web_relay_service._get_config()
        return jsonify({
            'success': True,
            'enabled': cfg['enabled'],
            'ip': cfg['ip'],
            'port': cfg['port'],
            'last_error': web_relay_service.last_error,
            'relays': web_relay_service.get_all_states()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/relay/<int:channel>/on', methods=['POST', 'GET'])
def relay_on(channel):
    """Turn relay ON"""
    if channel < 1 or channel > 8:
        return jsonify({'success': False, 'error': 'Invalid channel'}), 400

    success = relay_service.set_relay(channel, True)
    websocket_service.broadcast_barrier_status(relay_service.get_all_states())
    return jsonify({'success': success, 'state': True})


@api_bp.route('/api/relay/<int:channel>/off', methods=['POST', 'GET'])
def relay_off(channel):
    """Turn relay OFF"""
    if channel < 1 or channel > 8:
        return jsonify({'success': False, 'error': 'Invalid channel'}), 400

    success = relay_service.set_relay(channel, False)
    websocket_service.broadcast_barrier_status(relay_service.get_all_states())
    return jsonify({'success': success, 'state': False})


@api_bp.route('/api/relay/<int:channel>/pulse', methods=['POST', 'GET'])
def relay_pulse(channel):
    """Pulse relay"""
    if channel < 1 or channel > 8:
        return jsonify({'success': False, 'error': 'Invalid channel'}), 400

    duration = request.args.get('duration', config.barrier_pulse_duration, type=float)
    success = relay_service.pulse_relay(channel, duration)
    return jsonify({'success': success, 'state': 'pulsing', 'duration': duration})


@api_bp.route('/api/relay/all/on', methods=['POST'])
def all_relays_on():
    """Turn all relays ON"""
    relay_service.all_on()
    websocket_service.broadcast_barrier_status(relay_service.get_all_states())
    return jsonify({'success': True, 'state': True})


@api_bp.route('/api/relay/all/off', methods=['POST'])
def all_relays_off():
    """Turn all relays OFF"""
    relay_service.all_off()
    websocket_service.broadcast_barrier_status(relay_service.get_all_states())
    return jsonify({'success': True, 'state': False})


# ============== Sync ==============

@api_bp.route('/api/sync/status', methods=['GET'])
def get_sync_status():
    """Get sync status"""
    try:
        status = sync_service.get_status()
        status['ws_clients'] = websocket_service.get_client_count()
        return jsonify({'success': True, **status})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/sync/now', methods=['POST'])
def force_sync():
    """Force immediate sync"""
    try:
        sync_service.force_sync()
        return jsonify({'success': True, 'message': 'Sync started'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/sync/test', methods=['GET'])
def test_connection():
    """Test Odoo connection"""
    try:
        success, message = sync_service.test_connection()
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============== Config ==============

@api_bp.route('/api/config', methods=['GET'])
def get_config():
    """Get configuration (sensitive values masked)"""
    try:
        cfg = config.get_all()
        # Mask sensitive values
        masked = dict(cfg)
        for key in ['odoo_token']:
            if key in masked and masked[key]:
                masked[key] = '***'
        return jsonify({'success': True, 'config': masked})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/config', methods=['POST'])
def update_config():
    """Update configuration"""
    try:
        data = request.get_json()
        config.set_bulk(data)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============== Health ==============

@api_bp.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        status = sync_service.get_status()
        return jsonify({
            'success': True,
            'status': 'healthy',
            'odoo_connected': status['odoo_connected'],
            'vehicles_count': status['vehicles_count'],
            'ws_clients': websocket_service.get_client_count()
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'status': 'unhealthy',
            'error': str(e)
        }), 500


# ============== Images ==============

@api_bp.route('/images/<path:filename>')
def serve_image(filename):
    """Serve local images"""
    try:
        return send_from_directory(IMAGES_DIR, filename)
    except Exception as e:
        return jsonify({'error': 'Image not found'}), 404


# ============== S3 Storage ==============

@api_bp.route('/api/s3/status', methods=['GET'])
def get_s3_status():
    """Get S3 service status"""
    try:
        from services.s3_service import s3_service
        status = s3_service.get_status()
        return jsonify({'success': True, **status})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/s3/test', methods=['POST'])
def test_s3_connection():
    """Test S3 connection by uploading a test file"""
    try:
        from services.s3_service import s3_service
        success, message = s3_service.test_connection()
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============== System Management ==============

@api_bp.route('/api/system/clear-data', methods=['POST'])
def clear_all_data():
    """Clear all local data (vehicles, logs, cameras, etc.)"""
    try:
        from database.db import get_db

        conn = get_db()
        cursor = conn.cursor()

        # Clear all data tables
        cursor.execute('DELETE FROM vehicles')
        cursor.execute('DELETE FROM access_logs')
        cursor.execute('DELETE FROM locations')
        cursor.execute('DELETE FROM anpr_cameras')
        cursor.execute('DELETE FROM barrier_mapping')
        cursor.execute('DELETE FROM upload_queue')

        conn.commit()

        # Clear images directory
        import shutil
        if os.path.exists(IMAGES_DIR):
            for filename in os.listdir(IMAGES_DIR):
                filepath = os.path.join(IMAGES_DIR, filename)
                try:
                    if os.path.isfile(filepath):
                        os.unlink(filepath)
                except Exception:
                    pass

        logger.info("All local data cleared")
        return jsonify({
            'success': True,
            'message': 'All data cleared successfully'
        })

    except Exception as e:
        logger.error(f"Clear data error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/system/factory-reset', methods=['POST'])
def factory_reset():
    """Factory reset - clear all data AND logout"""
    try:
        from database.db import get_db

        conn = get_db()
        cursor = conn.cursor()

        # Clear all data tables
        cursor.execute('DELETE FROM vehicles')
        cursor.execute('DELETE FROM access_logs')
        cursor.execute('DELETE FROM locations')
        cursor.execute('DELETE FROM anpr_cameras')
        cursor.execute('DELETE FROM barrier_mapping')
        cursor.execute('DELETE FROM upload_queue')
        cursor.execute('DELETE FROM config')

        conn.commit()

        # Clear images
        import shutil
        if os.path.exists(IMAGES_DIR):
            for filename in os.listdir(IMAGES_DIR):
                filepath = os.path.join(IMAGES_DIR, filename)
                try:
                    if os.path.isfile(filepath):
                        os.unlink(filepath)
                except Exception:
                    pass

        # Logout from Odoo
        odoo_api.logout()
        config.clear_cache()

        logger.info("Factory reset completed")
        return jsonify({
            'success': True,
            'message': 'Factory reset completed'
        })

    except Exception as e:
        logger.error(f"Factory reset error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
