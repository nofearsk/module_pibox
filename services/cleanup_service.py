"""
Image Cleanup Service
Automatically deletes old images based on retention policy and disk usage.
"""
import os
import shutil
import logging
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class CleanupService:
    """Service for cleaning up old images and managing disk space"""

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
        self._thread = None
        self._running = False
        self.last_cleanup = None
        self.last_freed_mb = 0

    def start(self):
        """Start cleanup loop (runs every hour)"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._thread.start()
        logger.info("Cleanup service started")

    def stop(self):
        """Stop cleanup loop"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Cleanup service stopped")

    def _cleanup_loop(self):
        """Background loop - check every hour"""
        # Run first cleanup after 60 seconds
        time.sleep(60)
        while self._running:
            try:
                self.run_cleanup()
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
            # Sleep 1 hour between checks
            for _ in range(3600):
                if not self._running:
                    break
                time.sleep(1)

    def run_cleanup(self):
        """Run cleanup based on retention days and disk threshold"""
        from config import config, IMAGES_DIR

        if not os.path.exists(IMAGES_DIR):
            return

        retention_days = int(config.get('image_retention_days', 7))
        disk_threshold = int(config.get('disk_threshold_percent', 85))

        freed_bytes = 0

        # 1. Delete images older than retention days
        freed_bytes += self._delete_old_images(IMAGES_DIR, retention_days)

        # 2. If disk still above threshold, delete oldest images until below
        freed_bytes += self._free_disk_space(IMAGES_DIR, disk_threshold)

        freed_mb = round(freed_bytes / (1024 * 1024), 2)
        self.last_cleanup = datetime.now().isoformat()
        self.last_freed_mb = freed_mb

        if freed_mb > 0:
            logger.info(f"Cleanup freed {freed_mb} MB")

    def _delete_old_images(self, images_dir, retention_days):
        """Delete images older than retention_days"""
        freed = 0
        cutoff = datetime.now() - timedelta(days=retention_days)

        # Images are stored in YYYY/MM/DD structure
        try:
            for year_dir in sorted(os.listdir(images_dir)):
                year_path = os.path.join(images_dir, year_dir)
                if not os.path.isdir(year_path) or not year_dir.isdigit():
                    continue

                for month_dir in sorted(os.listdir(year_path)):
                    month_path = os.path.join(year_path, month_dir)
                    if not os.path.isdir(month_path) or not month_dir.isdigit():
                        continue

                    for day_dir in sorted(os.listdir(month_path)):
                        day_path = os.path.join(month_path, day_dir)
                        if not os.path.isdir(day_path) or not day_dir.isdigit():
                            continue

                        try:
                            dir_date = datetime(int(year_dir), int(month_dir), int(day_dir))
                            if dir_date < cutoff:
                                dir_size = self._get_dir_size(day_path)
                                shutil.rmtree(day_path)
                                freed += dir_size
                                logger.info(f"Deleted old images: {year_dir}/{month_dir}/{day_dir} ({round(dir_size/1024/1024, 1)} MB)")
                        except (ValueError, OSError) as e:
                            logger.debug(f"Skip {day_path}: {e}")

                    # Remove empty month dirs
                    if os.path.isdir(month_path) and not os.listdir(month_path):
                        os.rmdir(month_path)

                # Remove empty year dirs
                if os.path.isdir(year_path) and not os.listdir(year_path):
                    os.rmdir(year_path)

        except Exception as e:
            logger.error(f"Error deleting old images: {e}")

        return freed

    def _free_disk_space(self, images_dir, threshold_percent):
        """Delete oldest images until disk usage is below threshold"""
        freed = 0

        disk = self._get_disk_usage()
        if disk['percent'] < threshold_percent:
            return 0

        logger.warning(f"Disk usage {disk['percent']}% exceeds threshold {threshold_percent}%")

        # Get all date directories sorted oldest first
        date_dirs = self._get_date_dirs_sorted(images_dir)

        for dir_path in date_dirs:
            if self._get_disk_usage()['percent'] < threshold_percent:
                break

            try:
                dir_size = self._get_dir_size(dir_path)
                shutil.rmtree(dir_path)
                freed += dir_size
                logger.info(f"Freed disk space: deleted {dir_path} ({round(dir_size/1024/1024, 1)} MB)")
            except OSError as e:
                logger.error(f"Failed to delete {dir_path}: {e}")

        # Clean up empty parent dirs
        self._remove_empty_parents(images_dir)

        return freed

    def _get_date_dirs_sorted(self, images_dir):
        """Get all YYYY/MM/DD directories sorted oldest first"""
        dirs = []
        try:
            for year_dir in sorted(os.listdir(images_dir)):
                year_path = os.path.join(images_dir, year_dir)
                if not os.path.isdir(year_path) or not year_dir.isdigit():
                    continue
                for month_dir in sorted(os.listdir(year_path)):
                    month_path = os.path.join(year_path, month_dir)
                    if not os.path.isdir(month_path) or not month_dir.isdigit():
                        continue
                    for day_dir in sorted(os.listdir(month_path)):
                        day_path = os.path.join(month_path, day_dir)
                        if os.path.isdir(day_path) and day_dir.isdigit():
                            dirs.append(day_path)
        except Exception:
            pass
        return dirs

    def _remove_empty_parents(self, images_dir):
        """Remove empty year/month directories"""
        try:
            for year_dir in os.listdir(images_dir):
                year_path = os.path.join(images_dir, year_dir)
                if not os.path.isdir(year_path):
                    continue
                for month_dir in os.listdir(year_path):
                    month_path = os.path.join(year_path, month_dir)
                    if os.path.isdir(month_path) and not os.listdir(month_path):
                        os.rmdir(month_path)
                if os.path.isdir(year_path) and not os.listdir(year_path):
                    os.rmdir(year_path)
        except Exception:
            pass

    def _get_dir_size(self, path):
        """Get total size of a directory in bytes"""
        total = 0
        try:
            for dirpath, dirnames, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if os.path.isfile(fp):
                        total += os.path.getsize(fp)
        except Exception:
            pass
        return total

    def _get_disk_usage(self):
        """Get disk usage percentage"""
        try:
            stat = os.statvfs('/')
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bavail * stat.f_frsize
            used = total - free
            percent = (used / total * 100) if total > 0 else 0
            return {
                'total_gb': round(total / (1024**3), 1),
                'free_gb': round(free / (1024**3), 1),
                'percent': round(percent, 1)
            }
        except Exception:
            return {'total_gb': 0, 'free_gb': 0, 'percent': 0}

    def get_images_size(self):
        """Get total size of images directory"""
        from config import IMAGES_DIR
        if not os.path.exists(IMAGES_DIR):
            return {'size_mb': 0, 'file_count': 0}

        total_size = 0
        file_count = 0
        try:
            for dirpath, dirnames, filenames in os.walk(IMAGES_DIR):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if os.path.isfile(fp):
                        total_size += os.path.getsize(fp)
                        file_count += 1
        except Exception:
            pass

        return {
            'size_mb': round(total_size / (1024 * 1024), 1),
            'file_count': file_count
        }

    def get_status(self):
        """Get cleanup service status"""
        from config import config
        images_info = self.get_images_size()
        disk = self._get_disk_usage()
        return {
            'last_cleanup': self.last_cleanup,
            'last_freed_mb': self.last_freed_mb,
            'retention_days': int(config.get('image_retention_days', 7)),
            'disk_threshold': int(config.get('disk_threshold_percent', 85)),
            'images_size_mb': images_info['size_mb'],
            'images_count': images_info['file_count'],
            'disk_used_percent': disk['percent'],
            'disk_free_gb': disk['free_gb'],
        }


# Singleton
cleanup_service = CleanupService()
