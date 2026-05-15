"""
ANPR Camera Routes
Receives events from Hikvision and other ANPR cameras

Camera identification is done via reg_code (registration code) instead of IP address.
Each camera has a unique reg_code configured in Odoo, which maps to a location.
"""
from flask import request, jsonify
import base64
import logging
import xml.etree.ElementTree as ET

from . import anpr_bp
from services.anpr_service import anpr_service
from services.access_service import access_service
from services.websocket_service import websocket_service
from database.models import AnprCameraModel, LocationModel

logger = logging.getLogger(__name__)


# Field names commonly used by Dahua ITC firmwares for the plate crop vs
# the full-scene snapshot. Tried in order; first hit wins. The first two
# entries match what the ITC firmware on 192.168.1.113 actually sends.
_DAHUA_PLATE_IMAGE_KEYS = (
    'Picture.CutoutPic.Content',
    'Picture.Plate.Image',
    'PlateImage', 'PlateCutPic', 'plateImage', 'plate_image',
    'Plate.Data', 'Picture.PlateImage',
    'Picture.Plate.Data', 'Picture.Plate.Content',
)
_DAHUA_VEHICLE_IMAGE_KEYS = (
    'Picture.NormalPic.Content',
    'Picture.SceneImage',
    'Image', 'ImageData', 'SceneImage', 'BigImage', 'Picture.Data',
    'Picture.Image', 'Picture.Content',
    'Picture.Vehicle.Image',
)


def _walk_path(obj, dotted):
    cur = obj
    for part in dotted.split('.'):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _decode_b64(value):
    """Decode a base64 string (with or without 'data:image/...;base64,' prefix)."""
    if not isinstance(value, str) or len(value) < 100:
        return None
    s = value
    if s.startswith('data:') and ';base64,' in s:
        s = s.split(';base64,', 1)[1]
    try:
        raw = base64.b64decode(s, validate=False)
    except Exception:
        return None
    # Sanity: JPEG starts with FFD8, PNG with 89504E47
    if len(raw) > 4 and (raw[:2] == b'\xff\xd8' or raw[:4] == b'\x89PNG'):
        return raw
    return None


def _extract_dahua_image(data, prefer='plate'):
    """Find a base64 image somewhere in the Dahua JSON payload."""
    if not isinstance(data, dict):
        return None
    keys = _DAHUA_PLATE_IMAGE_KEYS if prefer == 'plate' else _DAHUA_VEHICLE_IMAGE_KEYS
    for path in keys:
        val = _walk_path(data, path)
        img = _decode_b64(val)
        if img:
            logger.info("Dahua image found at %s (%d bytes)", path, len(img))
            return img
    # Last-resort scan: find any long base64-looking string anywhere
    return _scan_for_b64_image(data)


def _scan_for_b64_image(obj, depth=0):
    if depth > 6:
        return None
    if isinstance(obj, str):
        return _decode_b64(obj)
    if isinstance(obj, dict):
        for v in obj.values():
            r = _scan_for_b64_image(v, depth + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _scan_for_b64_image(v, depth + 1)
            if r:
                return r
    return None


def _summarise(obj, depth=0, max_depth=3, max_str=40):
    """One-line structural map of a nested dict/list with truncated leaf previews."""
    if depth >= max_depth:
        return '…'
    if isinstance(obj, dict):
        return '{' + ', '.join(
            f'{k}={_summarise(v, depth+1, max_depth, max_str)}'
            for k, v in list(obj.items())[:20]
        ) + '}'
    if isinstance(obj, list):
        if not obj:
            return '[]'
        return f'[{len(obj)} × {_summarise(obj[0], depth+1, max_depth, max_str)}]'
    if isinstance(obj, str):
        if len(obj) > max_str:
            return f'<str len={len(obj)}>'
        return repr(obj)
    return repr(obj)


@anpr_bp.route('/hikfeed', methods=['GET', 'POST'])
@anpr_bp.route('/hikfeedv2', methods=['GET', 'POST'])
@anpr_bp.route('/hikfeed/<string:code>/<string:password>', methods=['GET', 'POST'])
@anpr_bp.route('/hikfeedv2/<string:code>/<string:password>', methods=['GET', 'POST'])
def hikfeed(code="", password=""):
    """
    Receive Hikvision ANPR events - matches Odoo format exactly

    URL formats:
    - /hikfeed/<code>/<password>
    - /hikfeedv2/<code>/<password>
    - /hikfeed?code=XXX&password=YYY

    Multipart files:
    - anpr.xml: Contains plate number in XML format
    - licensePlatePicture.jpg: Plate image
    - detectionPicture.jpg: Vehicle/detection image
    """
    try:
        camera_ip = request.remote_addr

        # Get code/password from URL path or query params
        if not code and 'code' in request.args:
            code = request.args.get('code', '')
            password = request.args.get('password', '')

        logger.info(f"Hikvision feed from {camera_ip}, code: {code}")
        logger.info(f"Content-Type: {request.content_type}")
        logger.info(f"Files received: {list(request.files.keys())}")

        plate = None
        # Store all images received (up to 6 possible files)
        images = {
            'plate': [],      # licensePlatePicture.jpg, licensePlatePicture_1.jpg
            'vehicle': []     # detectionPicture*.jpg, pedestrianDetectionPicture*.jpg
        }

        # Check if XML is in request body directly (not multipart)
        if not request.files and request.data:
            logger.info(f"No files, checking request body (length: {len(request.data)})")
            try:
                xml_data = request.data
                root = ET.fromstring(xml_data)
                for elem in root.iter():
                    local_tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                    if local_tag.lower() == 'licenseplate':
                        if elem.text and elem.text.strip():
                            plate = elem.text.strip().replace(' ', '').upper()
                            logger.info(f"Found plate in body XML: {plate}")
                            break
            except ET.ParseError as e:
                logger.error(f"Failed to parse body as XML: {e}")

        # Process multipart files
        for file_key in request.files:
            xml_file = request.files[file_key]
            filename = xml_file.filename or ''
            # Use filename for type detection - camera may use random IDs as form field keys
            logger.info(f"Processing file: key='{file_key}', filename='{filename}'")

            # Parse XML files (anpr.xml, ANPR.xml, or any .xml file)
            if filename.lower() == 'anpr.xml' or filename.lower().endswith('.xml'):
                xml_data = xml_file.read()
                logger.debug(f"Received XML data: {xml_data[:500]}...")
                try:
                    root = ET.fromstring(xml_data)
                    plate = None

                    # Method 1: Find licensePlate element (handles namespaces)
                    # Tag names with namespace look like: {http://...}licensePlate
                    for elem in root.iter():
                        # Get local tag name (strip namespace)
                        local_tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag

                        if local_tag.lower() == 'licenseplate':
                            if elem.text and elem.text.strip():
                                plate = elem.text.strip()
                                logger.info(f"Found plate in <{local_tag}>: {plate}")
                                break
                        elif local_tag.lower() == 'originallicenseplate' and not plate:
                            if elem.text and elem.text.strip():
                                plate = elem.text.strip()
                                logger.info(f"Found plate in <{local_tag}>: {plate}")

                    # Method 2: Try index-based access (original Odoo method for older cameras)
                    if not plate:
                        try:
                            plate = root[13][1].text
                            if plate:
                                logger.info(f"Found plate via index [13][1]: {plate}")
                        except (IndexError, TypeError):
                            pass

                    if plate:
                        plate = plate.replace(' ', '').upper()
                        logger.info(f"Extracted plate from XML: {plate}")

                except ET.ParseError as e:
                    logger.error(f"Failed to parse anpr.xml: {e}")

            # Plate images (licensePlatePicture.jpg, licensePlatePicture_1.jpg)
            elif filename.lower() in ['licenseplatepicture.jpg', 'licenseplatepicture_1.jpg']:
                img_data = xml_file.read()
                if img_data:
                    images['plate'].append({'filename': filename, 'data': img_data})
                    logger.info(f"Received plate image: {filename} ({len(img_data)} bytes)")

            # Detection/vehicle images (all 4 variants)
            elif filename.lower() in ['detectionpicture.jpg', 'detectionpicture_1.jpg',
                              'pedestriandetectionpicture.jpg', 'pedestriandetectionpicture_1.jpg']:
                img_data = xml_file.read()
                if img_data:
                    images['vehicle'].append({'filename': filename, 'data': img_data})
                    logger.info(f"Received vehicle image: {filename} ({len(img_data)} bytes)")

        # Check if we got a plate number
        if not plate:
            logger.warning(f"No plate number in Hikvision feed from {camera_ip}")
            return jsonify({'success': False, 'error': 'No plate number detected'}), 400

        # Look up camera by reg_code to get location_id
        camera = None
        location_id = None

        if code:
            camera = AnprCameraModel.get_by_reg_code(code)
            if camera:
                location_id = camera['location_id']
                logger.info(f"Camera identified: {camera['name']} (code: {code}, location_id: {location_id})")
                # Update heartbeat on ANPR event
                AnprCameraModel.update_heartbeat(code)
            else:
                logger.warning(f"Unknown camera code: {code}")

        # Log image counts
        logger.info(f"Images received: {len(images['plate'])} plate, {len(images['vehicle'])} vehicle")

        # Process vehicle access - pass all images
        result = access_service.process_vehicle(
            plate=plate,
            camera_ip=camera_ip,
            plate_images=images['plate'],    # List of {'filename': ..., 'data': ...}
            vehicle_images=images['vehicle'],  # List of {'filename': ..., 'data': ...}
            location_id=location_id,
            camera_name=camera['name'] if camera else None,
            reg_code=code  # For relay mapping from ANPR camera
        )

        # WebSocket broadcast is handled inside access_service.process_vehicle()

        return jsonify({
            'success': True,
            'plate': plate,
            'access_granted': result['access_granted'],
            'vehicle_type': result['vehicle_type'],
            'log_id': result['log_id'],
            'location_id': location_id,
            'camera_name': camera['name'] if camera else None
        })

    except Exception as e:
        logger.error(f"Error processing Hikvision feed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@anpr_bp.route('/api/anpr/dahua', methods=['POST'])
@anpr_bp.route('/api/anpr/dahua/<string:reg_code>', methods=['POST'])
@anpr_bp.route('/dahuafeed', methods=['POST'])
@anpr_bp.route('/dahuafeed/<string:reg_code>', methods=['POST'])
@anpr_bp.route('/dahuafeed/<string:reg_code>/<string:password>', methods=['POST'])
def dahua_event(reg_code=None, password=None):
    """
    Receive Dahua ITC ANPR events.

    Camera config: enable "Upload Picture → HTTP Server" and point it at
    `http://<pibox>:8080/api/anpr/dahua/<reg_code>` (or the unsuffixed URL).

    The camera can deliver:
    - application/json
    - application/x-www-form-urlencoded
    - multipart/form-data (image parts + a JSON metadata part — common for
      the "HTTP Picture Server" path on ITC cameras)
    """
    try:
        camera_ip = request.remote_addr
        content_type = (request.content_type or '').lower()
        logger.info("Dahua event from %s reg=%s ct=%s len=%s",
                    camera_ip, reg_code, content_type,
                    request.content_length)

        plate_image_bytes = None
        vehicle_image_bytes = None
        data = None

        if 'multipart' in content_type:
            # ITC HTTP Picture Server: one or more image parts + a JSON
            # text part. Field names vary by firmware; sniff for either.
            for fname, fstorage in request.files.items():
                blob = fstorage.read()
                if not blob:
                    continue
                lname = (fname or '').lower()
                if not plate_image_bytes and ('plate' in lname or 'snap' in lname):
                    plate_image_bytes = blob
                elif not vehicle_image_bytes:
                    vehicle_image_bytes = blob
                else:
                    plate_image_bytes = plate_image_bytes or blob
            # JSON metadata may arrive in form fields like "info", "data",
            # "metadata", or just as a stringified blob.
            form_kv = dict(request.form)
            for key in ('info', 'data', 'metadata', 'Picture', 'Event'):
                raw = form_kv.get(key)
                if raw and isinstance(raw, str) and raw.strip().startswith('{'):
                    try:
                        import json
                        data = json.loads(raw)
                        break
                    except Exception:
                        pass
            if data is None:
                data = form_kv
        elif 'json' in content_type:
            data = request.get_json(force=True, silent=True) or {}
        else:
            data = dict(request.form) if request.form else {}
            # Some firmwares post a bare JSON body with form content-type.
            if not data and request.data:
                try:
                    import json
                    data = json.loads(request.data.decode('utf-8', 'ignore'))
                except Exception:
                    data = {}

        logger.info("Dahua event payload keys: %s", list(data.keys()) if isinstance(data, dict) else type(data).__name__)
        if isinstance(data, dict) and logger.isEnabledFor(logging.DEBUG) is False:
            # One-line structural summary so we can see image-field paths
            # without dumping megabytes of base64. INFO-level so it shows up.
            logger.info("Dahua event structure: %s", _summarise(data))

        # Extract any base64 images embedded in the JSON.
        if not plate_image_bytes:
            plate_image_bytes = _extract_dahua_image(data, prefer='plate')
        if not vehicle_image_bytes:
            vehicle_image_bytes = _extract_dahua_image(data, prefer='vehicle')

        event = anpr_service.parse_dahua_event(data)
        event['camera_ip'] = camera_ip

        if not event.get('plate'):
            # Heartbeats / keepalives have no plate — silently 200 so the
            # camera doesn't think we're broken and back off.
            if isinstance(data, dict) and (data.get('Active') == 'keepAlive'
                                            or data.get('Action') == 'Heartbeat'):
                return jsonify({'success': True, 'noop': 'keepalive'}), 200
            logger.warning("Dahua event has no plate; payload keys: %s",
                           list(data.keys()) if isinstance(data, dict) else None)
            return jsonify({'success': False, 'error': 'No plate number detected'}), 400

        # Look up camera by reg_code (if URL supplied one) so we can attach
        # location / relay channels just like the Hikvision push path does.
        camera = None
        camera_id = None
        location_id = None
        camera_name = None
        if reg_code:
            from database.models import AnprCameraModel
            camera = AnprCameraModel.get_by_reg_code(reg_code)
            if camera:
                camera_id = camera['id']
                location_id = camera['location_id']
                camera_name = camera['name']
                try:
                    AnprCameraModel.update_heartbeat(reg_code)
                    AnprCameraModel.record_capture(camera_id, event['plate'])
                except Exception:
                    pass

        plate_images = []
        if plate_image_bytes:
            plate_images.append({'filename': 'plate.jpg', 'data': plate_image_bytes})
        vehicle_images = []
        if vehicle_image_bytes:
            vehicle_images.append({'filename': 'vehicle.jpg', 'data': vehicle_image_bytes})

        result = access_service.process_vehicle(
            plate=event['plate'],
            camera_ip=camera_ip,
            plate_images=plate_images,
            vehicle_images=vehicle_images,
            location_id=location_id,
            camera_name=camera_name,
            reg_code=reg_code,
        )

        return jsonify({
            'success': True,
            'plate': event['plate'],
            'confidence': event.get('confidence'),
            'access_granted': result['access_granted'],
            'log_id': result['log_id']
        })

    except Exception as e:
        logger.error(f"Error processing Dahua event: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@anpr_bp.route('/api/anpr/generic', methods=['POST'])
def generic_event():
    """
    Generic ANPR endpoint for simple plate notifications

    Expected JSON:
    {
        "plate": "ABC1234",
        "reg_code": "5R0MOI7X9NS24H",     (camera registration code)
        "reg_password": "xxx",             (camera registration password)
        "plate_image": "base64...",        (optional)
        "vehicle_image": "base64..."       (optional)
    }

    Camera is identified by reg_code, which maps to a location in Odoo.
    """
    try:
        data = request.get_json(force=True)

        plate = data.get('plate') or data.get('number') or data.get('plateNumber')
        if not plate:
            return jsonify({'success': False, 'error': 'No plate number provided'}), 400

        # Get camera by reg_code
        reg_code = data.get('reg_code') or data.get('regCode')
        reg_password = data.get('reg_password') or data.get('regPassword')

        camera = None
        location_id = None

        if reg_code:
            camera = AnprCameraModel.get_by_reg_code(reg_code)
            if camera:
                location_id = camera['location_id']
                logger.info(f"Camera identified: {camera['name']} (reg_code: {reg_code}, location_id: {location_id})")
                # Update heartbeat on ANPR event
                AnprCameraModel.update_heartbeat(reg_code)
            else:
                logger.warning(f"Unknown camera reg_code: {reg_code}")

        # Decode images if provided
        plate_image = None
        vehicle_image = None

        if data.get('plate_image'):
            import base64
            plate_image = base64.b64decode(data['plate_image'])

        if data.get('vehicle_image'):
            import base64
            vehicle_image = base64.b64decode(data['vehicle_image'])

        result = access_service.process_vehicle(
            plate=plate,
            camera_ip=request.remote_addr,
            plate_image=plate_image,
            vehicle_image=vehicle_image,
            location_id=location_id,
            camera_name=camera['name'] if camera else None,
            reg_code=reg_code  # For relay mapping from ANPR camera
        )

        # WebSocket broadcast is handled inside access_service.process_vehicle()

        return jsonify({
            'success': True,
            'plate': plate,
            'access_granted': result['access_granted'],
            'vehicle_type': result['vehicle_type'],
            'log_id': result['log_id'],
            'location_id': location_id,
            'camera_name': camera['name'] if camera else None
        })

    except Exception as e:
        logger.error(f"Error processing generic event: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@anpr_bp.route('/api/anpr/heartbeat', methods=['POST', 'GET'])
@anpr_bp.route('/api/anpr/heartbeat/<string:reg_code>', methods=['POST', 'GET'])
def camera_heartbeat(reg_code=None):
    """
    Camera heartbeat endpoint - cameras should call every 60-90 seconds

    URL formats:
    - /api/anpr/heartbeat/<reg_code>
    - /api/anpr/heartbeat?reg_code=XXX

    Returns camera status and any pending commands
    """
    try:
        if not reg_code:
            reg_code = request.args.get('reg_code') or request.args.get('code')

        if not reg_code:
            return jsonify({'success': False, 'error': 'reg_code required'}), 400

        camera = AnprCameraModel.get_by_reg_code(reg_code)
        if not camera:
            logger.warning(f"Heartbeat from unknown camera: {reg_code}")
            return jsonify({'success': False, 'error': 'Unknown camera'}), 404

        # Update heartbeat timestamp
        AnprCameraModel.update_heartbeat(reg_code)
        logger.debug(f"Heartbeat received from {camera['name']} ({reg_code})")

        return jsonify({
            'success': True,
            'camera_name': camera['name'],
            'reg_code': reg_code,
            'timestamp': __import__('datetime').datetime.now().isoformat()
        })

    except Exception as e:
        logger.error(f"Heartbeat error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@anpr_bp.route('/api/anpr/test', methods=['POST', 'GET'])
def test_event():
    """
    Test endpoint for simulating ANPR events

    GET/POST with ?plate=ABC1234&reg_code=5R0MOI7X9NS24H
    """
    plate = request.args.get('plate') or request.form.get('plate') or 'TEST123'
    reg_code = request.args.get('reg_code') or request.form.get('reg_code')

    camera = None
    location_id = None

    if reg_code:
        camera = AnprCameraModel.get_by_reg_code(reg_code)
        if camera:
            location_id = camera['location_id']

    result = access_service.process_vehicle(
        plate=plate,
        camera_ip=request.remote_addr,
        location_id=location_id,
        camera_name=camera['name'] if camera else None,
        reg_code=reg_code  # For relay mapping from ANPR camera
    )

    # WebSocket broadcast is handled inside access_service.process_vehicle()

    return jsonify({
        'success': True,
        'plate': plate,
        'access_granted': result['access_granted'],
        'vehicle_type': result['vehicle_type'],
        'log_id': result['log_id'],
        'location_id': location_id,
        'camera_name': camera['name'] if camera else None,
        'relay_channels': result.get('barriers_triggered', []),
        'test': True
    })
