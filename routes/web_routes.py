"""
Web UI Routes
Serves HTML pages for the web interface
"""
from functools import wraps
from flask import render_template, redirect, url_for, request
import logging

from . import web_bp
from database.models import VehicleModel, BarrierModel, AccessLogModel, AnprCameraModel, LocationModel
import json
from services.relay_service import relay_service
from services.sync_service import sync_service
from services.websocket_service import websocket_service
from config import config

logger = logging.getLogger(__name__)


def require_auth(f):
    """Decorator to require Odoo authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not config.is_configured:
            return redirect(url_for('web.login'))
        return f(*args, **kwargs)
    return decorated_function


@web_bp.route('/login')
def login():
    """Login page"""
    # If already configured, redirect to dashboard
    if config.is_configured:
        return redirect(url_for('web.dashboard'))

    return render_template('login.html',
        odoo_url=config.odoo_url,
        username=config.odoo_username
    )


@web_bp.route('/logout')
def logout():
    """Logout and clear token"""
    try:
        from services.odoo_api import odoo_api
        odoo_api.logout()
    except Exception as e:
        logger.error(f"Logout error: {e}")

    return redirect(url_for('web.login'))


@web_bp.route('/')
@require_auth
def dashboard():
    """Dashboard page"""
    try:
        stats = AccessLogModel.get_today_stats()
        recent_logs = AccessLogModel.get_recent(10)
        sync_status = sync_service.get_status()
        relay_states = relay_service.get_all_states()
        barriers = BarrierModel.get_all()

        return render_template('dashboard.html',
            stats=stats,
            recent_logs=recent_logs,
            sync_status=sync_status,
            relay_states=relay_states,
            barriers=barriers,
            ws_port=8081
        )
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        return render_template('error.html', error=str(e))


@web_bp.route('/vehicles')
@require_auth
def vehicles():
    """Vehicles list page"""
    try:
        vehicles = VehicleModel.get_all()
        return render_template('vehicles.html',
            vehicles=vehicles,
            total=len(vehicles)
        )
    except Exception as e:
        logger.error(f"Vehicles page error: {e}")
        return render_template('error.html', error=str(e))


@web_bp.route('/logs')
@require_auth
def logs():
    """Access logs page"""
    try:
        logs = AccessLogModel.get_recent(100)
        stats = AccessLogModel.get_today_stats()
        return render_template('logs.html',
            logs=logs,
            stats=stats
        )
    except Exception as e:
        logger.error(f"Logs page error: {e}")
        return render_template('error.html', error=str(e))


@web_bp.route('/barriers')
@require_auth
def barriers():
    """Barrier mapping page"""
    try:
        barriers = BarrierModel.get_all()
        relay_states = relay_service.get_all_states()

        # Get ANPR cameras with relay info
        anpr_cameras_raw = AnprCameraModel.get_all()
        anpr_cameras = []
        for cam in anpr_cameras_raw:
            cam_dict = dict(cam)
            # Parse relay_channels to list
            relay_list = []
            if cam['relay_channels']:
                try:
                    relay_list = json.loads(cam['relay_channels'])
                except:
                    relay_list = [int(cam['relay_channels'])] if cam['relay_channels'] else []
            cam_dict['relay_list'] = relay_list
            # Get location name
            if cam['location_id']:
                loc = LocationModel.get_by_odoo_id(cam['location_id'])
                cam_dict['location_name'] = loc['name'] if loc else None
            else:
                cam_dict['location_name'] = None
            anpr_cameras.append(cam_dict)

        return render_template('barriers.html',
            barriers=barriers,
            relay_states=relay_states,
            anpr_cameras=anpr_cameras
        )
    except Exception as e:
        logger.error(f"Barriers page error: {e}")
        return render_template('error.html', error=str(e))


@web_bp.route('/settings')
@require_auth
def settings():
    """Settings page"""
    try:
        cfg = config.get_all()
        sync_status = sync_service.get_status()
        return render_template('settings.html',
            config=cfg,
            sync_status=sync_status
        )
    except Exception as e:
        logger.error(f"Settings page error: {e}")
        return render_template('error.html', error=str(e))
