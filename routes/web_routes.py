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
from database.models import VehicleModel, BarrierModel, AccessLogModel, AnprCameraModel, LocationModel, AuditLogModel, BlacklistModel
import json
from datetime import date, timedelta
from services.relay_service import relay_service
from services.sync_service import sync_service
from services.websocket_service import websocket_service
from services import system_health
from config import config


def audit_log(action, details=None, resource_type=None, resource_id=None):
    """Helper to create audit log entry"""
    try:
        user = 'admin' if session.get('admin_logged_in') else 'anonymous'
        ip = request.remote_addr
        AuditLogModel.log(action, user=user, ip_address=ip, details=details,
                          resource_type=resource_type, resource_id=resource_id)
    except Exception as e:
        logger.error(f"Audit log error: {e}")

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
            audit_log('admin_login', details='Login successful')
            if config.is_configured:
                return redirect(url_for('web.dashboard'))
            return redirect(url_for('web.login'))
        else:
            error = 'Invalid password'
            audit_log('admin_login_failed', details='Invalid password')

    return render_template('admin_login.html', error=error)


@web_bp.route('/admin-logout')
def admin_logout():
    """Admin logout"""
    audit_log('admin_logout', details='User logged out')
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
    """Access logs page with pagination and filters"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        search = request.args.get('search', '').strip()
        vehicle_type = request.args.get('type', '').strip()
        date_from = request.args.get('date_from', '').strip()
        date_to = request.args.get('date_to', '').strip()
        access_filter = request.args.get('access', '').strip()

        # Parse access filter
        access_granted = None
        if access_filter == 'granted':
            access_granted = True
        elif access_filter == 'denied':
            access_granted = False

        # Limit per_page to reasonable values
        per_page = min(max(per_page, 10), 100)

        total = AccessLogModel.count(
            vehicle_type=vehicle_type if vehicle_type else None,
            search=search if search else None,
            date_from=date_from if date_from else None,
            date_to=date_to if date_to else None,
            access_granted=access_granted
        )
        logs_list = AccessLogModel.get_paginated(
            page=page,
            per_page=per_page,
            vehicle_type=vehicle_type if vehicle_type else None,
            search=search if search else None,
            date_from=date_from if date_from else None,
            date_to=date_to if date_to else None,
            access_granted=access_granted
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
            vehicle_type=vehicle_type,
            date_from=date_from,
            date_to=date_to,
            access_filter=access_filter
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


# ==================== System Health ====================

@web_bp.route('/health')
@require_auth
def health():
    """System health page"""
    try:
        health_data = system_health.get_all_health()
        return render_template('health.html', health=health_data)
    except Exception as e:
        logger.error(f"Health page error: {e}")
        return render_template('error.html', error=str(e))


# ==================== Blacklist Management ====================

@web_bp.route('/blacklist')
@require_auth
def blacklist():
    """Blacklist management page"""
    try:
        entries = BlacklistModel.get_all(active_only=False)
        count = BlacklistModel.count()
        return render_template('blacklist.html', entries=entries, count=count)
    except Exception as e:
        logger.error(f"Blacklist page error: {e}")
        return render_template('error.html', error=str(e))


@web_bp.route('/blacklist/add', methods=['POST'])
@require_auth
def blacklist_add():
    """Add plate to blacklist"""
    try:
        plate = request.form.get('plate', '').strip().upper()
        reason = request.form.get('reason', '').strip()
        expires_days = request.form.get('expires_days', type=int)

        if not plate:
            return redirect(url_for('web.blacklist'))

        expires_at = None
        if expires_days and expires_days > 0:
            expires_at = (date.today() + timedelta(days=expires_days)).isoformat()

        BlacklistModel.add(plate, reason=reason, added_by='admin', expires_at=expires_at)
        audit_log('blacklist_add', details=f'Added {plate}', resource_type='blacklist', resource_id=plate)

        return redirect(url_for('web.blacklist'))
    except Exception as e:
        logger.error(f"Blacklist add error: {e}")
        return render_template('error.html', error=str(e))


@web_bp.route('/blacklist/remove/<int:entry_id>', methods=['POST'])
@require_auth
def blacklist_remove(entry_id):
    """Remove plate from blacklist"""
    try:
        BlacklistModel.delete(entry_id)
        audit_log('blacklist_remove', details=f'Removed entry {entry_id}', resource_type='blacklist', resource_id=str(entry_id))
        return redirect(url_for('web.blacklist'))
    except Exception as e:
        logger.error(f"Blacklist remove error: {e}")
        return render_template('error.html', error=str(e))


# ==================== Audit Logs ====================

@web_bp.route('/audit')
@require_auth
def audit():
    """Audit logs page"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        search = request.args.get('search', '').strip()
        action = request.args.get('action', '').strip()
        date_from = request.args.get('date_from', '').strip()
        date_to = request.args.get('date_to', '').strip()

        per_page = min(max(per_page, 10), 100)

        total = AuditLogModel.count(
            action=action if action else None,
            search=search if search else None,
            date_from=date_from if date_from else None,
            date_to=date_to if date_to else None
        )
        logs_list = AuditLogModel.get_paginated(
            page=page,
            per_page=per_page,
            action=action if action else None,
            search=search if search else None,
            date_from=date_from if date_from else None,
            date_to=date_to if date_to else None
        )

        total_pages = (total + per_page - 1) // per_page if total > 0 else 1
        actions = AuditLogModel.get_actions()

        return render_template('audit.html',
            logs=logs_list,
            total=total,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            search=search,
            action=action,
            date_from=date_from,
            date_to=date_to,
            actions=actions
        )
    except Exception as e:
        logger.error(f"Audit page error: {e}")
        return render_template('error.html', error=str(e))


# ==================== Statistics Dashboard ====================

@web_bp.route('/stats')
@require_auth
def stats():
    """Statistics dashboard page"""
    try:
        today = date.today()
        yesterday = today - timedelta(days=1)
        week_ago = today - timedelta(days=7)

        # Basic stats
        today_stats = AccessLogModel.get_today_stats()
        yesterday_stats = AccessLogModel.get_stats_by_date_range(yesterday.isoformat(), yesterday.isoformat())
        week_stats = AccessLogModel.get_stats_by_date_range(week_ago.isoformat(), today.isoformat())

        # Detailed analytics
        hourly_stats = AccessLogModel.get_hourly_stats()
        daily_stats = AccessLogModel.get_daily_stats(days=7)
        camera_stats = AccessLogModel.get_camera_stats(days=7)
        top_vehicles = AccessLogModel.get_top_vehicles(limit=10, days=7)
        peak_hours = AccessLogModel.get_peak_hours(days=7)
        recent_denied = AccessLogModel.get_recent_denied(limit=5)

        # Get counts
        vehicle_count = VehicleModel.count()
        blacklist_count = BlacklistModel.count()
        camera_count = AnprCameraModel.count()

        # Calculate percentages
        today_rate = round((today_stats['granted'] / today_stats['total'] * 100) if today_stats['total'] > 0 else 0, 1)
        week_rate = round((week_stats['granted'] / week_stats['total'] * 100) if week_stats['total'] > 0 else 0, 1)

        # Today vs yesterday comparison
        today_vs_yesterday = today_stats['total'] - yesterday_stats['total']

        return render_template('stats.html',
            today=today_stats,
            yesterday=yesterday_stats,
            week=week_stats,
            hourly=hourly_stats,
            daily=daily_stats,
            camera_stats=camera_stats,
            top_vehicles=top_vehicles,
            peak_hours=peak_hours,
            recent_denied=recent_denied,
            vehicle_count=vehicle_count,
            blacklist_count=blacklist_count,
            camera_count=camera_count,
            today_rate=today_rate,
            week_rate=week_rate,
            today_vs_yesterday=today_vs_yesterday
        )
    except Exception as e:
        logger.error(f"Stats page error: {e}")
        return render_template('error.html', error=str(e))
