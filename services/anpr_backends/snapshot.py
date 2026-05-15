"""
Snapshot backend — polls an HTTP JPEG URL at a configured interval,
runs local ANPR, and routes results through the access pipeline.

Moved here from the old `snapshot_poller.py`. Backward-compatible shim
lives in `services/snapshot_poller.py`.
"""
import logging
import threading
import time
from datetime import datetime
from urllib.parse import urlparse, urlunparse

import requests

from .base import BaseBackend

logger = logging.getLogger(__name__)


# Debounce window — same plate from same camera within this many seconds
# is treated as one event, not repeated vehicles.
DUPLICATE_WINDOW_SECONDS = 8


class SnapshotBackend(BaseBackend):
    mode = 'snapshot'

    def __init__(self):
        self._workers = {}        # camera_id -> Thread
        self._stop_flags = {}     # camera_id -> Event
        self._last_plate = {}     # camera_id -> (plate, datetime)
        self._lock = threading.Lock()

    # ---------- public ----------

    def active_cameras(self):
        return [cid for cid, t in self._workers.items() if t.is_alive()]

    def start_camera(self, cam):
        camera_id = cam['id']
        with self._lock:
            existing = self._workers.get(camera_id)
            if existing and existing.is_alive():
                return
            stop = threading.Event()
            t = threading.Thread(
                target=self._worker_loop,
                args=(camera_id, stop),
                name=f'SnapshotWorker-{camera_id}',
                daemon=True,
            )
            self._workers[camera_id] = t
            self._stop_flags[camera_id] = stop
            t.start()
            logger.info("Snapshot: started worker for camera id=%s (%s)",
                        camera_id, cam.get('name'))

    def stop_camera(self, camera_id):
        with self._lock:
            ev = self._stop_flags.pop(camera_id, None)
            self._workers.pop(camera_id, None)
        if ev:
            ev.set()
            logger.info("Snapshot: stopped worker for camera id=%s", camera_id)

    # ---------- worker ----------

    def _worker_loop(self, camera_id, stop_event):
        from database.models import AnprCameraModel
        while not stop_event.is_set():
            row = AnprCameraModel.get_by_id(camera_id)
            if not row or not row['feed_enabled'] or row['feed_mode'] != self.mode \
                    or not row['snapshot_url']:
                return
            # sqlite3.Row has no .get(); use a dict so logging/extra fields work
            cam = dict(row)
            interval = max(1, int(cam['poll_interval_seconds'] or 2))
            try:
                self._poll_once(cam)
            except Exception as e:
                logger.warning("Snapshot poll failed for camera %s: %s", camera_id, e)
                try:
                    AnprCameraModel.record_poll_error(camera_id, str(e))
                except Exception:
                    pass
            for _ in range(interval * 10):
                if stop_event.is_set():
                    return
                time.sleep(0.1)

    def _poll_once(self, cam):
        from database.models import AnprCameraModel
        from services.lpr_service import lpr_service
        from services.access_service import access_service

        url = cam['snapshot_url']
        # min_confidence here is the detector threshold; combined OCR score
        # is filtered separately below using min_read_score.
        min_conf = 0.25
        camera_id = cam['id']

        image_bytes = _fetch_snapshot(url)
        if not image_bytes:
            return

        # Crop to detect_region if configured — same field as RTSP mode
        region = _parse_region(cam.get('detect_region'))
        analyze_bytes = image_bytes
        region_offset = (0, 0)
        if region is not None:
            cropped, off = _crop_jpeg_bytes(image_bytes, region)
            if cropped is not None:
                analyze_bytes = cropped
                region_offset = off

        result = lpr_service.analyze(analyze_bytes, min_confidence=min_conf)
        if not result:
            return

        plate = result['plate']
        score = float(result['confidence'])

        # Apply min_read_score filter (same semantic as RTSP mode)
        min_read = float(cam.get('min_read_score') or 0.4)
        if score < min_read:
            logger.info("Snapshot %s candidate %s score=%.2f below min_read %.2f, skip",
                        cam.get('name'), plate, score, min_read)
            return

        # Sliding-window dedupe using per-camera config (same as RTSP mode)
        cooldown = int(cam.get('max_valid_detect_seconds') or 30)
        prev = self._last_plate.get(camera_id)
        now = datetime.now()
        if prev and prev[0] == plate and (now - prev[1]).total_seconds() < cooldown:
            self._last_plate[camera_id] = (plate, now)  # slide window
            return
        self._last_plate[camera_id] = (plate, now)

        # Build plate crop + full image from the original full-resolution bytes
        plate_bytes = b''
        bbox = result.get('bbox')
        if bbox and len(bbox) == 4:
            full_bbox = [bbox[0] + region_offset[0], bbox[1] + region_offset[1],
                         bbox[2] + region_offset[0], bbox[3] + region_offset[1]]
            plate_bytes = _crop_jpeg_to_jpeg(image_bytes, full_bbox, pad=10) or b''

        plate_images = []
        vehicle_images = []
        if plate_bytes:
            plate_images.append({'filename': 'plate.jpg', 'data': plate_bytes})
            vehicle_images.append({'filename': 'vehicle.jpg', 'data': image_bytes})
        else:
            plate_images.append({'filename': 'plate.jpg', 'data': image_bytes})

        AnprCameraModel.record_capture(camera_id, plate)
        AnprCameraModel.update_heartbeat_by_id(camera_id)

        logger.info("Snapshot ANPR: camera=%s plate=%s score=%.2f (plate_img=%dB scene_img=%dB)",
                    cam.get('name'), plate, score, len(plate_bytes), len(image_bytes))

        access_service.process_vehicle(
            plate=plate,
            camera_ip=_host_of(url),
            plate_images=plate_images,
            vehicle_images=vehicle_images,
            location_id=cam['location_id'],
            camera_name=cam['name'],
            reg_code=cam['reg_code'],
        )


def _fetch_snapshot(url, timeout=5):
    """GET an image URL. Tries HTTP Basic auth first, falls back to Digest
    if the camera requires it (Dahua CGI endpoints typically do)."""
    parsed = urlparse(url)
    user, pw = parsed.username or '', parsed.password or ''
    if user or pw:
        netloc = parsed.hostname or ''
        if parsed.port:
            netloc = f'{netloc}:{parsed.port}'
        url = urlunparse(parsed._replace(netloc=netloc))

    auth = (user, pw) if (user or pw) else None
    resp = requests.get(url, auth=auth, timeout=timeout, stream=False, verify=False)
    # If basic was rejected, retry with digest (Dahua CGI behaviour)
    if resp.status_code == 401 and auth:
        from requests.auth import HTTPDigestAuth
        resp = requests.get(url, auth=HTTPDigestAuth(user, pw),
                            timeout=timeout, stream=False, verify=False)
    resp.raise_for_status()
    ct = (resp.headers.get('Content-Type') or '').lower()
    if not (ct.startswith('image/') or ct == 'application/octet-stream' or not ct):
        raise RuntimeError(f'Unexpected content-type: {ct}')
    return resp.content


def _host_of(url):
    try:
        return urlparse(url).hostname or ''
    except Exception:
        return ''


def _parse_region(region):
    """Parse 'x1 y1 x2 y2' string into list of ints, or None."""
    if not region:
        return None
    parts = str(region).replace(',', ' ').split()
    if len(parts) != 4:
        return None
    try:
        return [int(float(p)) for p in parts]
    except (TypeError, ValueError):
        return None


def _crop_jpeg_bytes(jpeg_bytes, region):
    """Decode JPEG, crop to [x1,y1,x2,y2], re-encode. Returns (bytes, (x1,y1))."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None, (0, 0)
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        return None, (0, 0)
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = region
    x1 = max(0, x1); y1 = max(0, y1)
    x2 = min(w, x2); y2 = min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return None, (0, 0)
    crop = frame[y1:y2, x1:x2]
    ok, buf = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        return None, (0, 0)
    return buf.tobytes(), (x1, y1)


def _crop_jpeg_to_jpeg(jpeg_bytes, bbox, pad=0):
    """Decode JPEG, crop to bbox + padding, re-encode. Returns JPEG bytes or None."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        return None
    h, w = frame.shape[:2]
    x1 = max(0, int(bbox[0]) - pad)
    y1 = max(0, int(bbox[1]) - pad)
    x2 = min(w, int(bbox[2]) + pad)
    y2 = min(h, int(bbox[3]) + pad)
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2]
    ok, buf = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        return None
    return buf.tobytes()
