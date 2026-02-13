"""
S3 Upload Service
Handles uploading images to S3-compatible storage
"""
import os
import logging
import threading
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

# Try to import boto3, but don't fail if not installed
try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    logger.warning("boto3 not installed - S3 uploads disabled")


class S3Service:
    """Service for uploading images to S3"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._client = None
        self._upload_queue = []

    def _get_config(self):
        """Get S3 config"""
        from config import config
        return {
            'enabled': config.get('s3_enabled', '') == '1',
            'bucket': config.get('s3_bucket', ''),
            'access_key': config.get('s3_access_key', ''),
            'secret_key': config.get('s3_secret_key', ''),
            'region': config.get('s3_region', 'ap-southeast-1'),
            'endpoint': config.get('s3_endpoint', ''),  # For S3-compatible services
            'prefix': config.get('s3_prefix', 'anpr'),  # Folder prefix in bucket
            'public_domain': config.get('s3_public_domain', ''),  # Custom domain for public URLs (R2, etc.)
        }

    @property
    def is_configured(self):
        """Check if S3 is configured"""
        if not BOTO3_AVAILABLE:
            return False
        cfg = self._get_config()
        # Ensure we return a boolean, not a string
        return bool(cfg['enabled'] and cfg['bucket'] and cfg['access_key'] and cfg['secret_key'])

    def _get_client(self):
        """Get or create S3 client"""
        if not BOTO3_AVAILABLE:
            return None

        cfg = self._get_config()
        if not cfg['enabled']:
            return None

        try:
            kwargs = {
                'aws_access_key_id': cfg['access_key'],
                'aws_secret_access_key': cfg['secret_key'],
                'region_name': cfg['region'],
            }

            # Custom endpoint for S3-compatible services (MinIO, DigitalOcean Spaces, etc.)
            if cfg['endpoint']:
                kwargs['endpoint_url'] = cfg['endpoint']

            return boto3.client('s3', **kwargs)
        except Exception as e:
            logger.error(f"Failed to create S3 client: {e}")
            return None

    def generate_image_uuid(self, prefix='img'):
        """Generate unique image filename"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        return f"{prefix}_{timestamp}_{unique_id}"

    def get_s3_url(self, image_uuid, image_type='plate'):
        """
        Get the S3 URL for an image (before it's uploaded)

        Args:
            image_uuid: The unique image identifier
            image_type: 'plate' or 'vehicle'

        Returns:
            str: Full S3 URL or None if not configured
        """
        cfg = self._get_config()
        if not cfg['enabled'] or not cfg['bucket']:
            return None

        # Build the S3 key
        date_prefix = datetime.now().strftime('%Y/%m/%d')
        key = f"{cfg['prefix']}/{date_prefix}/{image_type}/{image_uuid}.jpg"

        # Build URL - priority: public_domain > endpoint > AWS S3
        if cfg['public_domain']:
            # Custom public domain (e.g., Cloudflare R2 with custom domain)
            base_url = cfg['public_domain'].rstrip('/')
            # Remove protocol if present and re-add https
            if base_url.startswith('http://') or base_url.startswith('https://'):
                return f"{base_url}/{key}"
            else:
                return f"https://{base_url}/{key}"
        elif cfg['endpoint']:
            # Custom endpoint (MinIO, DigitalOcean Spaces, etc.)
            base_url = cfg['endpoint'].rstrip('/')
            return f"{base_url}/{cfg['bucket']}/{key}"
        else:
            # Standard AWS S3
            return f"https://{cfg['bucket']}.s3.{cfg['region']}.amazonaws.com/{key}"

    def save_local(self, image_data, image_uuid, image_type='plate'):
        """
        Save image locally

        Args:
            image_data: bytes - image content
            image_uuid: str - unique identifier
            image_type: str - 'plate' or 'vehicle'

        Returns:
            str: Local file path relative to IMAGES_DIR
        """
        from config import IMAGES_DIR

        # Create date-based subdirectory
        date_dir = datetime.now().strftime('%Y/%m/%d')
        full_dir = os.path.join(IMAGES_DIR, date_dir, image_type)
        os.makedirs(full_dir, exist_ok=True)

        # Save file
        filename = f"{image_uuid}.jpg"
        filepath = os.path.join(full_dir, filename)

        with open(filepath, 'wb') as f:
            f.write(image_data)

        # Return relative path
        return os.path.join(date_dir, image_type, filename)

    def upload_to_s3(self, image_data, image_uuid, image_type='plate'):
        """
        Upload image to S3

        Args:
            image_data: bytes - image content
            image_uuid: str - unique identifier
            image_type: str - 'plate' or 'vehicle'

        Returns:
            tuple: (success: bool, url: str or error message)
        """
        if not self.is_configured:
            return False, "S3 not configured"

        cfg = self._get_config()
        client = self._get_client()
        if not client:
            return False, "Failed to create S3 client"

        try:
            # Build S3 key
            date_prefix = datetime.now().strftime('%Y/%m/%d')
            key = f"{cfg['prefix']}/{date_prefix}/{image_type}/{image_uuid}.jpg"

            # Upload with public-read ACL
            client.put_object(
                Bucket=cfg['bucket'],
                Key=key,
                Body=image_data,
                ContentType='image/jpeg',
                ACL='public-read',
            )

            url = self.get_s3_url(image_uuid, image_type)
            logger.info(f"Uploaded to S3: {key}")
            return True, url

        except ClientError as e:
            error_msg = str(e)
            logger.error(f"S3 upload failed: {error_msg}")
            return False, error_msg
        except Exception as e:
            error_msg = str(e)
            logger.error(f"S3 upload error: {error_msg}")
            return False, error_msg

    def upload_async(self, image_data, image_uuid, image_type='plate', local_path=None):
        """
        Upload image to S3 in background thread

        Args:
            image_data: bytes - image content
            image_uuid: str - unique identifier
            image_type: str - 'plate' or 'vehicle'
            local_path: str - local file path to delete after successful upload
        """
        if not self.is_configured:
            return

        def upload_thread():
            success, result = self.upload_to_s3(image_data, image_uuid, image_type)
            if success:
                # Delete local file after successful S3 upload
                if local_path:
                    try:
                        from config import IMAGES_DIR
                        full_path = os.path.join(IMAGES_DIR, local_path)
                        if os.path.exists(full_path):
                            os.remove(full_path)
                            logger.info(f"Deleted local file after S3 upload: {local_path}")
                    except Exception as e:
                        logger.warning(f"Failed to delete local file {local_path}: {e}")
            else:
                # Queue for retry
                from database.models import UploadQueueModel
                import json
                import base64
                UploadQueueModel.add('s3_image', json.dumps({
                    'image_data_b64': base64.b64encode(image_data).decode(),
                    'image_uuid': image_uuid,
                    'image_type': image_type,
                    'local_path': local_path
                }))

        thread = threading.Thread(target=upload_thread, daemon=True)
        thread.start()

    def test_connection(self):
        """
        Test S3 connection by uploading a small test file

        Returns:
            tuple: (success: bool, message: str)
        """
        if not BOTO3_AVAILABLE:
            return False, "boto3 not installed. Run: pip install boto3"

        cfg = self._get_config()
        if not cfg['enabled']:
            return False, "S3 is not enabled in settings"

        if not cfg['bucket'] or not cfg['access_key'] or not cfg['secret_key']:
            return False, "Missing S3 credentials (bucket, access_key, or secret_key)"

        client = self._get_client()
        if not client:
            return False, "Failed to create S3 client"

        try:
            # Create test content
            test_uuid = self.generate_image_uuid('test')
            test_content = b"PiBox S3 connection test"
            test_key = f"{cfg['prefix']}/test/{test_uuid}.txt"

            # Upload test file
            client.put_object(
                Bucket=cfg['bucket'],
                Key=test_key,
                Body=test_content,
                ContentType='text/plain',
            )

            # Try to delete it
            client.delete_object(
                Bucket=cfg['bucket'],
                Key=test_key,
            )

            return True, f"S3 connection successful! Bucket: {cfg['bucket']}"

        except NoCredentialsError:
            return False, "Invalid credentials"
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            return False, f"S3 error ({error_code}): {error_msg}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def process_pending_uploads(self):
        """Process pending S3 uploads from queue"""
        from database.models import UploadQueueModel
        import json
        import base64

        pending = UploadQueueModel.get_pending('s3_image', limit=10)
        for item in pending:
            try:
                payload = json.loads(item['payload'])
                image_data = base64.b64decode(payload['image_data_b64'])
                success, result = self.upload_to_s3(
                    image_data,
                    payload['image_uuid'],
                    payload['image_type']
                )
                if success:
                    UploadQueueModel.mark_completed(item['id'])
                else:
                    UploadQueueModel.mark_failed(item['id'], result)
            except Exception as e:
                UploadQueueModel.mark_failed(item['id'], str(e))

    def get_status(self):
        """Get S3 service status"""
        cfg = self._get_config()
        return {
            'boto3_available': BOTO3_AVAILABLE,
            'enabled': cfg['enabled'],
            'configured': self.is_configured,
            'bucket': cfg['bucket'] if cfg['bucket'] else None,
            'region': cfg['region'],
            'endpoint': cfg['endpoint'] if cfg['endpoint'] else None,
            'prefix': cfg['prefix'],
            'public_domain': cfg['public_domain'] if cfg['public_domain'] else None,
        }


# Singleton instance
s3_service = S3Service()
