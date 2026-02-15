"""
ANPR Camera Routes
Receives events from Hikvision and other ANPR cameras

Camera identification is done via reg_code (registration code) instead of IP address.
Each camera has a unique reg_code configured in Odoo, which maps to a location.
"""
from flask import request, jsonify
import logging
import xml.etree.ElementTree as ET

from . import anpr_bp
from services.anpr_service import anpr_service
from services.access_service import access_service
from services.websocket_service import websocket_service
from database.models import AnprCameraModel, LocationModel

logger = logging.getLogger(__name__)


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
def dahua_event():
    """
    Receive Dahua ANPR events (for future use)
    """
    try:
        camera_ip = request.remote_addr
        data = request.get_json(force=True) if request.is_json else dict(request.form)

        event = anpr_service.parse_dahua_event(data)
        event['camera_ip'] = camera_ip

        if not event.get('plate'):
            return jsonify({'success': False, 'error': 'No plate number detected'}), 400

        result = access_service.process_vehicle(
            plate=event['plate'],
            camera_ip=camera_ip,
            plate_image=event.get('plate_image'),  # Legacy single image
            vehicle_image=event.get('vehicle_image')  # Legacy single image
        )

        return jsonify({
            'success': True,
            'plate': event['plate'],
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
