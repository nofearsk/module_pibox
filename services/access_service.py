"""
Access Control Service
Handles access decisions based on vehicle lookup
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class AccessService:
    """Service for access control decisions"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def process_vehicle(self, plate, camera_ip=None, plate_images=None, vehicle_images=None,
                        location_id=None, camera_name=None, reg_code=None,
                        plate_image=None, vehicle_image=None):
        """
        Process a detected vehicle and make access decision

        Args:
            plate: str - detected plate number
            camera_ip: str - IP of camera that detected vehicle (optional, for relay mapping)
            plate_images: list - list of {'filename': str, 'data': bytes} for plate images
            vehicle_images: list - list of {'filename': str, 'data': bytes} for vehicle images
            location_id: int - Odoo location ID (from camera's reg_code lookup)
            camera_name: str - Camera name for logging
            plate_image: bytes - (legacy) single plate image data
            vehicle_image: bytes - (legacy) single vehicle image data

        Returns:
            dict: {
                'access_granted': bool,
                'vehicle_type': str ('resident', 'unknown'),
                'vehicle_info': dict or None,
                'barriers_triggered': list,
                'log_id': int,
                'location_id': int
            }
        """
        from database.models import VehicleModel, BarrierModel, AccessLogModel, AnprCameraModel
        from services.relay_service import relay_service
        from services.anpr_service import anpr_service
        from services.websocket_service import websocket_service
        from services.sync_service import sync_service
        from services.s3_service import s3_service
        from config import config

        result = {
            'access_granted': False,
            'vehicle_type': 'unknown',
            'vehicle_info': None,
            'barriers_triggered': [],
            'log_id': None,
            'location_id': location_id
        }

        # Normalize plate
        normalized_plate = anpr_service.normalize_plate(plate)
        if not normalized_plate:
            logger.warning("Empty plate number received")
            return result

        logger.info(f"Processing vehicle: {normalized_plate} from camera {camera_ip}")

        # Handle legacy single image parameters
        if plate_images is None:
            plate_images = []
        if vehicle_images is None:
            vehicle_images = []

        # Convert legacy single images to list format
        if plate_image and not plate_images:
            plate_images = [{'filename': 'plate.jpg', 'data': plate_image}]
        if vehicle_image and not vehicle_images:
            vehicle_images = [{'filename': 'vehicle.jpg', 'data': vehicle_image}]

        # Save ALL images with UUID and upload to S3
        # Track first image URLs for Odoo (main images)
        plate_image_path = None
        vehicle_image_path = None
        plate_image_s3_url = None
        vehicle_image_s3_url = None

        # Process all plate images
        for i, img in enumerate(plate_images):
            img_data = img.get('data')
            filename = img.get('filename', f'plate_{i}.jpg')

            if not img_data:
                continue

            # Generate UUID for this image
            img_uuid = s3_service.generate_image_uuid('plate')

            # Save locally first
            local_path = s3_service.save_local(img_data, img_uuid, 'plate')
            logger.info(f"Saved plate image: {filename} -> {local_path}")

            # Use first image as the main one for Odoo
            if i == 0:
                plate_image_path = local_path
                plate_image_s3_url = s3_service.get_s3_url(img_uuid, 'plate')

            # Upload to S3 in background (will delete local file after success)
            if s3_service.is_configured:
                s3_service.upload_async(img_data, img_uuid, 'plate', local_path)
                logger.debug(f"Queued S3 upload for plate image: {img_uuid}")

        # Process all vehicle/detection images
        for i, img in enumerate(vehicle_images):
            img_data = img.get('data')
            filename = img.get('filename', f'vehicle_{i}.jpg')

            if not img_data:
                continue

            # Generate UUID for this image
            img_uuid = s3_service.generate_image_uuid('vehicle')

            # Save locally first
            local_path = s3_service.save_local(img_data, img_uuid, 'vehicle')
            logger.info(f"Saved vehicle image: {filename} -> {local_path}")

            # Use first image as the main one for Odoo
            if i == 0:
                vehicle_image_path = local_path
                vehicle_image_s3_url = s3_service.get_s3_url(img_uuid, 'vehicle')

            # Upload to S3 in background (will delete local file after success)
            if s3_service.is_configured:
                s3_service.upload_async(img_data, img_uuid, 'vehicle', local_path)
                logger.debug(f"Queued S3 upload for vehicle image: {img_uuid}")

        logger.info(f"Processed {len(plate_images)} plate images, {len(vehicle_images)} vehicle images")

        # Look up vehicle in local database
        vehicle = VehicleModel.get_by_plate(normalized_plate)

        if vehicle and VehicleModel.is_valid(vehicle):
            # Vehicle found and valid - grant access
            result['access_granted'] = True
            result['vehicle_type'] = 'resident'
            result['vehicle_info'] = {
                'plate': vehicle['plate'],
                'unit_name': vehicle['unit_name'],
                'owner_name': vehicle['owner_name'],
                'unit_id': vehicle['unit_id']
            }

            # Get relay channels - try ANPR camera first, then barrier_mapping
            relay_channels = []
            if reg_code:
                relay_channels = AnprCameraModel.get_relay_channels(reg_code)
            if not relay_channels and camera_ip:
                relay_channels = BarrierModel.get_relay_channels(camera_ip)
            result['barriers_triggered'] = relay_channels

            # Trigger barriers
            pulse_duration = config.barrier_pulse_duration
            relay_service.pulse_multiple(relay_channels, pulse_duration)

            logger.info(f"Access GRANTED for {normalized_plate} - {vehicle['owner_name']} ({vehicle['unit_name']})")

        else:
            # Unknown vehicle - deny access
            result['access_granted'] = False
            result['vehicle_type'] = 'unknown'
            logger.info(f"Access DENIED for {normalized_plate} - unknown vehicle")

        # Get camera/barrier name for logging
        display_name = camera_name
        if not display_name and camera_ip:
            barrier_mapping = BarrierModel.get_by_camera_ip(camera_ip)
            display_name = barrier_mapping['camera_name'] if barrier_mapping else camera_ip

        # Create access log with camera and relay info
        log_id = AccessLogModel.create(
            plate=normalized_plate,
            camera_ip=camera_ip,
            access_granted=result['access_granted'],
            vehicle_type=result['vehicle_type'],
            unit_name=result['vehicle_info']['unit_name'] if result['vehicle_info'] else None,
            owner_name=result['vehicle_info']['owner_name'] if result['vehicle_info'] else None,
            image_path=plate_image_path or vehicle_image_path,
            camera_name=display_name,
            relay_triggered=result['barriers_triggered']
        )
        result['log_id'] = log_id

        # Broadcast via WebSocket
        websocket_service.broadcast_access_event({
            'id': log_id,
            'plate': normalized_plate,
            'timestamp': datetime.now().isoformat(),
            'access_granted': result['access_granted'],
            'vehicle_type': result['vehicle_type'],
            'unit_name': result['vehicle_info']['unit_name'] if result['vehicle_info'] else None,
            'owner_name': result['vehicle_info']['owner_name'] if result['vehicle_info'] else None,
            'image_url': f"/images/{plate_image_path}" if plate_image_path else None,
            'camera_name': display_name,
            'location_id': location_id,
            'barriers_triggered': result['barriers_triggered']
        })

        # Push to Odoo asynchronously (ALL events - granted or denied)
        # Use S3 URLs if configured, otherwise local URLs
        sync_service.push_access_log_async(
            log_id=log_id,
            plate=normalized_plate,
            camera_ip=camera_ip,
            access_granted=result['access_granted'],
            vehicle_type=result['vehicle_type'],
            plate_image_url=plate_image_s3_url or (f"/images/{plate_image_path}" if plate_image_path else None),
            vehicle_image_url=vehicle_image_s3_url or (f"/images/{vehicle_image_path}" if vehicle_image_path else None),
            location_name=display_name,
            location_id=location_id,  # Pass location_id directly
            unit_id=result['vehicle_info']['unit_id'] if result['vehicle_info'] else None,
            iu_number=vehicle['iu_number'] if vehicle else None
        )

        return result

    def manual_grant_access(self, camera_ip=None, relay_channels=None):
        """
        Manually grant access (trigger barriers)

        Args:
            camera_ip: str - Camera IP to get relay mapping (optional)
            relay_channels: list - Direct relay channels to trigger (optional)
        """
        from database.models import BarrierModel
        from services.relay_service import relay_service
        from config import config

        if relay_channels is None:
            if camera_ip:
                relay_channels = BarrierModel.get_relay_channels(camera_ip)
            else:
                relay_channels = [1]

        pulse_duration = config.barrier_pulse_duration
        relay_service.pulse_multiple(relay_channels, pulse_duration)

        logger.info(f"Manual access granted - triggered relays {relay_channels}")
        return relay_channels

    def check_plate(self, plate):
        """
        Check if a plate is registered (without triggering access)

        Returns:
            dict: Vehicle info if found, None otherwise
        """
        from database.models import VehicleModel
        from services.anpr_service import anpr_service

        normalized_plate = anpr_service.normalize_plate(plate)
        vehicle = VehicleModel.get_by_plate(normalized_plate)

        if vehicle and VehicleModel.is_valid(vehicle):
            return {
                'plate': vehicle['plate'],
                'unit_name': vehicle['unit_name'],
                'owner_name': vehicle['owner_name'],
                'valid': True
            }
        elif vehicle:
            return {
                'plate': vehicle['plate'],
                'unit_name': vehicle['unit_name'],
                'owner_name': vehicle['owner_name'],
                'valid': False,
                'reason': 'Expired or inactive'
            }
        return None


# Singleton instance
access_service = AccessService()
