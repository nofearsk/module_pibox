"""
ANPR Service
Parses ANPR camera events (Hikvision, Dahua, etc.)
"""
import re
import os
import base64
import logging
from datetime import datetime
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)


class ANPRService:
    """Service for parsing ANPR camera events"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def parse_hikvision_event(self, data, content_type=None):
        """
        Parse Hikvision ANPR event
        Supports both XML and multipart/form-data formats

        Returns:
            dict: {
                'plate': str,
                'confidence': float,
                'plate_image': bytes or None,
                'vehicle_image': bytes or None,
                'timestamp': datetime,
                'direction': str,
                'camera_ip': str
            }
        """
        result = {
            'plate': None,
            'confidence': 0,
            'plate_image': None,
            'vehicle_image': None,
            'timestamp': datetime.now(),
            'direction': None,
            'camera_ip': None
        }

        try:
            # Check if it's XML data
            if isinstance(data, str) and data.strip().startswith('<'):
                result.update(self._parse_hikvision_xml(data))
            elif isinstance(data, bytes) and data.strip().startswith(b'<'):
                result.update(self._parse_hikvision_xml(data.decode('utf-8', errors='ignore')))
            elif isinstance(data, dict):
                result.update(self._parse_hikvision_dict(data))
            else:
                # Try to parse as form data
                result.update(self._parse_hikvision_form(data))

        except Exception as e:
            logger.error(f"Error parsing Hikvision event: {e}")

        return result

    def _parse_hikvision_xml(self, xml_data):
        """Parse Hikvision XML format"""
        result = {}
        try:
            # Handle namespace
            xml_data = re.sub(r'xmlns="[^"]+"', '', xml_data)
            root = ET.fromstring(xml_data)

            # Find plate number
            plate_elem = root.find('.//licensePlate') or root.find('.//plateNumber')
            if plate_elem is not None and plate_elem.text:
                result['plate'] = plate_elem.text.strip().upper()

            # Find confidence
            conf_elem = root.find('.//confidence') or root.find('.//plateConfidence')
            if conf_elem is not None and conf_elem.text:
                result['confidence'] = float(conf_elem.text)

            # Find direction
            dir_elem = root.find('.//direction') or root.find('.//vehicleDirection')
            if dir_elem is not None and dir_elem.text:
                result['direction'] = dir_elem.text.lower()

            # Find timestamp
            time_elem = root.find('.//dateTime') or root.find('.//captureTime')
            if time_elem is not None and time_elem.text:
                try:
                    result['timestamp'] = datetime.fromisoformat(
                        time_elem.text.replace('Z', '+00:00')
                    )
                except ValueError:
                    pass

            # Find images (base64 encoded)
            plate_img = root.find('.//licensePlatePicture') or root.find('.//plateImage')
            if plate_img is not None and plate_img.text:
                result['plate_image'] = base64.b64decode(plate_img.text)

            vehicle_img = root.find('.//vehiclePicture') or root.find('.//vehicleImage')
            if vehicle_img is not None and vehicle_img.text:
                result['vehicle_image'] = base64.b64decode(vehicle_img.text)

        except ET.ParseError as e:
            logger.error(f"XML parse error: {e}")

        return result

    def _parse_hikvision_dict(self, data):
        """Parse Hikvision JSON/dict format"""
        result = {}

        # Various field names used by different Hikvision models
        plate_fields = ['licensePlate', 'plateNumber', 'plate', 'number', 'vehicleNo']
        for field in plate_fields:
            if field in data and data[field]:
                result['plate'] = str(data[field]).strip().upper()
                break

        if 'confidence' in data:
            result['confidence'] = float(data['confidence'])

        if 'direction' in data:
            result['direction'] = str(data['direction']).lower()

        # Handle images
        if 'licensePlatePicture' in data:
            img_data = data['licensePlatePicture']
            if isinstance(img_data, str):
                result['plate_image'] = base64.b64decode(img_data)
            elif isinstance(img_data, bytes):
                result['plate_image'] = img_data

        if 'vehiclePicture' in data:
            img_data = data['vehiclePicture']
            if isinstance(img_data, str):
                result['vehicle_image'] = base64.b64decode(img_data)
            elif isinstance(img_data, bytes):
                result['vehicle_image'] = img_data

        return result

    def _parse_hikvision_form(self, data):
        """Parse form data format"""
        result = {}
        if hasattr(data, 'get'):
            plate = data.get('licensePlate') or data.get('plateNumber') or data.get('number')
            if plate:
                result['plate'] = str(plate).strip().upper()
        return result

    def parse_dahua_event(self, data):
        """Parse Dahua ANPR event (for future use)"""
        result = {
            'plate': None,
            'confidence': 0,
            'plate_image': None,
            'vehicle_image': None,
            'timestamp': datetime.now(),
            'direction': None,
            'camera_ip': None
        }

        try:
            if isinstance(data, dict):
                # Dahua typically uses 'PlateNumber' field
                if 'PlateNumber' in data:
                    result['plate'] = str(data['PlateNumber']).strip().upper()
                elif 'plateNumber' in data:
                    result['plate'] = str(data['plateNumber']).strip().upper()

                if 'Confidence' in data:
                    result['confidence'] = float(data['Confidence'])

        except Exception as e:
            logger.error(f"Error parsing Dahua event: {e}")

        return result

    def save_image(self, image_data, plate, image_type='plate'):
        """
        Save image to local storage

        Args:
            image_data: bytes
            plate: str - plate number for filename
            image_type: 'plate' or 'vehicle'

        Returns:
            str: Relative path to saved image
        """
        if not image_data:
            return None

        try:
            from config import IMAGES_DIR

            # Create date-based directory
            today = datetime.now().strftime('%Y%m%d')
            dir_path = os.path.join(IMAGES_DIR, today)
            os.makedirs(dir_path, exist_ok=True)

            # Generate filename
            timestamp = datetime.now().strftime('%H%M%S')
            safe_plate = re.sub(r'[^a-zA-Z0-9]', '', plate)
            filename = f"{safe_plate}_{timestamp}_{image_type}.jpg"
            filepath = os.path.join(dir_path, filename)

            # Save image
            with open(filepath, 'wb') as f:
                f.write(image_data)

            # Return relative path for URL
            return f"{today}/{filename}"

        except Exception as e:
            logger.error(f"Error saving image: {e}")
            return None

    def normalize_plate(self, plate):
        """
        Normalize plate number for comparison
        - Remove spaces and special characters
        - Convert to uppercase
        """
        if not plate:
            return None
        return re.sub(r'[^A-Z0-9]', '', plate.upper())


# Singleton instance
anpr_service = ANPRService()
