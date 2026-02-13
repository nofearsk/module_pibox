"""
Web UI Routes
Serves HTML pages for the web interface
"""
from functools import wraps
from flask import render_template, redirect, url_for, request, session
import logging
import hashlib
import secrets

from . import web_bp
from database.models import VehicleModel, BarrierModel, AccessLogModel, AnprCameraModel, LocationModel
import json
from services.relay_service import relay_service
from services.sync_service import sync_service
from services.websocket_service import websocket_service
from config import config

logger = logging.getLogger(__name__)


def hash_password(password):
    """Hash password with SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()


def require_admin(f):
    """Decorator to require local admin authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if admin password is set
        admin_hash = config.get('admin_password_hash', '')
        if not admin_hash:
            # No password set - redirect to setup
            return redirect(url_for('web.admin_setup'))

        # Check if logged in
        if not session.get('admin_logged_in'):
            return redirect(url_for('web.admin_login'))

        return f(*args, **kwargs)
    return decorated_function


def require_auth(f):
    """Decorator to require Odoo authentication (after admin auth)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # First check admin auth
        admin_hash = config.get('admin_password_hash', '')
        if not admin_hash:
            return redirect(url_for('web.admin_setup'))
        if not session.get('admin_logged_in'):
            return redirect(url_for('web.admin_login'))

        # Then check Odoo auth
        if not config.is_configured:
            return redirect(url_for('web.login'))
        return f(*args, **kwargs)
    return decorated_function


# ==================== Admin Authentication ====================

@web_bp.route('/admin-setup', methods=['GET', 'POST'])
def admin_setup():
    """Initial admin password setup"""
    # If password already set, redirect to login
    if config.get('admin_password_hash', ''):
        return redirect(url_for('web.admin_login'))

    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if len(password) < 4:
            error = 'Password must be at least 4 characters'
        elif password != confirm:
            error = 'Passwords do not match'
        else:
            # Save hashed password
            config.set('admin_password_hash', hash_password(password))
            session['admin_logged_in'] = True
            logger.info("Admin password set successfully")
            return redirect(url_for('web.login'))

    return render_template('admin_setup.html', error=error)


@web_bp.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page"""
    # If no password set, redirect to setup
    if not config.get('admin_password_hash', ''):
        return redirect(url_for('web.admin_setup'))

    # If already logged in, redirect appropriately
    if session.get('admin_logged_in'):
        if config.is_configured:
            return redirect(url_for('web.dashboard'))
        return redirect(url_for('web.login'))

    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        stored_hash = config.get('admin_password_hash', '')

        if hash_password(password) == stored_hash:
            session['admin_logged_in'] = True
            logger.info("Admin login successful")
            if config.is_configured:
                return redirect(url_for('web.dashboard'))
            return redirect(url_for('web.login'))
        else:
            error = 'Invalid password'

    return render_template('admin_login.html', error=error)


@web_bp.route('/admin-logout')
def admin_logout():
    """Admin logout"""
    session.pop('admin_logged_in', None)
    return redirect(url_for('web.admin_login'))


@web_bp.route('/login')
@require_admin
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
    """Vehicles list page with pagination"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        search = request.args.get('search', '').strip()

        # Limit per_page to reasonable values
        per_page = min(max(per_page, 10), 100)

        total = VehicleModel.count(search=search if search else None)
        vehicles = VehicleModel.get_paginated(page=page, per_page=per_page, search=search if search else None)

        total_pages = (total + per_page - 1) // per_page  # Ceiling division

        return render_template('vehicles.html',
            vehicles=vehicles,
            total=total,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            search=search
        )
    except Exception as e:
        logger.error(f"Vehicles page error: {e}")
        return render_template('error.html', error=str(e))


@web_bp.route('/logs')
@require_auth
def logs():
    """Access logs page with pagination"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        search = request.args.get('search', '').strip()
        vehicle_type = request.args.get('type', '').strip()

        # Limit per_page to reasonable values
        per_page = min(max(per_page, 10), 100)

        total = AccessLogModel.count(
            vehicle_type=vehicle_type if vehicle_type else None,
            search=search if search else None
        )
        logs_list = AccessLogModel.get_paginated(
            page=page,
            per_page=per_page,
            vehicle_type=vehicle_type if vehicle_type else None,
            search=search if search else None
        )

        total_pages = (total + per_page - 1) // per_page if total > 0 else 1

        stats = AccessLogModel.get_today_stats()

        return render_template('logs.html',
            logs=logs_list,
            stats=stats,
            total=total,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            search=search,
            vehicle_type=vehicle_type
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


@web_bp.route('/camera-feed')
@require_auth
def camera_feed():
    """Live camera feed page with WebSocket subscriptions"""
    try:
        return render_template('camera_feed.html')
    except Exception as e:
        logger.error(f"Camera feed page error: {e}")
        return render_template('error.html', error=str(e))
