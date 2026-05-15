"""
RTSP backend — pulls frames from an RTSP stream, crops to a detect_region,
runs local OCR (via lpr_service), scores hits over a time window, and
routes the best plate through the access pipeline.

Heavier than the snapshot backend (continuous video instead of polled
JPEGs), so only spin up workers for cameras explicitly set to feed_mode='rtsp'.

OpenCV (cv2) and the LPR engine are imported lazily — if either is
missing, the backend reports `available=False` and refuses to start
workers, so the rest of PiBox keeps running.
"""
import logging
import os
import re
import threading
import time
from datetime import datetime
from difflib import SequenceMatcher

from .base import BaseBackend

# Tell FFmpeg (used by OpenCV under the hood) to keep latency near zero:
# no input buffering, low-delay decode, force TCP for stable RTSP.
# This MUST be set before the first cv2.VideoCapture() call in this process.
os.environ.setdefault(
    'OPENCV_FFMPEG_CAPTURE_OPTIONS',
    'rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;0|reorder_queue_size;0',
)

logger = logging.getLogger(__name__)

PLATE_REGEX = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,3}[A-Z]?$")
DUPLICATE_WINDOW_SECONDS = 8
WHITELIST_REFRESH_SECONDS = 30


try:
    import cv2  # noqa: F401
    import numpy as np  # noqa: F401
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False


class _Capture:
    """Threaded RTSP reader with auto-reconnect. Always serves the latest frame."""

    def __init__(self, url):
        self.url = url
        self.frame = None
        self.error = None
        self.running = True
        self._cap = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        import cv2
        while self.running:
            self._cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not self._cap.isOpened():
                self.error = 'open_failed'
                time.sleep(5)
                continue
            # Keep only one frame in the internal queue so cap.read() always
            # returns the latest frame, not whatever's been queued up.
            try:
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            self.error = None
            while self.running:
                ok, f = self._cap.read()
                if not ok:
                    self.error = 'read_failed'
                    try:
                        self._cap.release()
                    except Exception:
                        pass
                    time.sleep(2)
                    break
                with self._lock:
                    self.frame = f

    def read(self):
        with self._lock:
            return self.frame

    def clear(self):
        with self._lock:
            self.frame = None

    def release(self):
        self.running = False
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass


class RtspBackend(BaseBackend):
    mode = 'rtsp'

    def __init__(self):
        self._workers = {}        # camera_id -> Thread
        self._stop_flags = {}     # camera_id -> Event
        self._last_plate = {}     # camera_id -> (plate, datetime)
        self._latest_frame = {}   # camera_id -> numpy.ndarray (latest frame)
        self._lock = threading.Lock()

    def get_latest_frame(self, camera_id):
        """Return the latest decoded frame for an active worker, or None."""
        return self._latest_frame.get(camera_id)

    # ---------- public ----------

    def active_cameras(self):
        return [cid for cid, t in self._workers.items() if t.is_alive()]

    def get_status(self):
        return {
            'mode': self.mode,
            'available': _CV2_AVAILABLE and self._lpr_ready(),
            'active': list(self.active_cameras()),
        }

    def start_camera(self, cam):
        if not _CV2_AVAILABLE:
            logger.warning("RTSP backend unavailable (cv2 missing) — skipping camera %s",
                           cam.get('id'))
            return
        if not self._lpr_ready():
            logger.warning("RTSP backend unavailable (LPR engine not loaded) — skipping camera %s",
                           cam.get('id'))
            return

        camera_id = cam['id']
        with self._lock:
            existing = self._workers.get(camera_id)
            if existing and existing.is_alive():
                return
            stop = threading.Event()
            t = threading.Thread(
                target=self._worker_loop,
                args=(camera_id, stop),
                name=f'RtspWorker-{camera_id}',
                daemon=True,
            )
            self._workers[camera_id] = t
            self._stop_flags[camera_id] = stop
            t.start()
            logger.info("RTSP: started worker for camera id=%s (%s)",
                        camera_id, cam.get('name'))

    def stop_camera(self, camera_id):
        with self._lock:
            ev = self._stop_flags.pop(camera_id, None)
            self._workers.pop(camera_id, None)
            self._latest_frame.pop(camera_id, None)
        if ev:
            ev.set()
            logger.info("RTSP: stopped worker for camera id=%s", camera_id)

    # ---------- helpers ----------

    def _lpr_ready(self):
        try:
            from services.lpr_service import lpr_service
            status = lpr_service.get_status()
            return bool(status.get('loaded'))
        except Exception:
            return False

    def _load_lists(self):
        """Pull whitelist + blacklist plates from local SQLite."""
        from database.models import VehicleModel, BlacklistModel
        whitelist = [v['plate'].upper() for v in VehicleModel.get_all() if v['plate']]
        blacklist = [b['plate'].upper() for b in BlacklistModel.get_all() if b['plate']]
        return whitelist, blacklist

    def _validate(self, plate, whitelist, blacklist, min_ratio):
        """Fuzzy-match a detected plate against blacklist then whitelist."""
        for number in blacklist:
            if SequenceMatcher(None, number, plate).ratio() > min_ratio:
                return False, number
        best, best_ratio = None, 0.0
        for number in whitelist:
            r = SequenceMatcher(None, number, plate).ratio()
            if r > min_ratio and r > best_ratio:
                best, best_ratio = number, r
        if best is not None:
            return True, best
        return False, None

    # ---------- worker ----------

    def _worker_loop(self, camera_id, stop_event):
        from database.models import AnprCameraModel
        import cv2

        cap = None
        try:
            while not stop_event.is_set():
                row = AnprCameraModel.get_by_id(camera_id)
                if (not row or not row['feed_enabled']
                        or row['feed_mode'] != self.mode
                        or not row['rtsp_url']):
                    return
                # sqlite3.Row has no .get(); use dict throughout so logging works
                cam = dict(row)

                if cap is None or cap.url != cam['rtsp_url']:
                    if cap is not None:
                        cap.release()
                    cap = _Capture(cam['rtsp_url'])
                    time.sleep(2)  # let stream open

                try:
                    self._process_camera(cam, cap, stop_event)
                except Exception as e:
                    logger.warning("RTSP loop for camera %s failed: %s", camera_id, e)
                    AnprCameraModel.record_poll_error(camera_id, str(e))
                    time.sleep(5)
        finally:
            if cap is not None:
                cap.release()

    def _process_camera(self, cam, cap, stop_event):
        """Inner loop — scores plate detections across a time window."""
        from database.models import AnprCameraModel
        from services.lpr_service import lpr_service
        from services.access_service import access_service
        import cv2

        camera_id = cam['id']
        initial_url = cam['rtsp_url']
        min_read = float(cam['min_read_score'] or 0.8)
        min_ratio = float(cam['min_ratio_score'] or 0.85)
        max_first = int(cam['max_first_detect_seconds'] or 3)
        max_last = int(cam['max_last_detect_seconds'] or 5)
        max_valid = int(cam['max_valid_detect_seconds'] or 10)
        region = self._parse_region(cam['detect_region'])

        whitelist, blacklist = self._load_lists()
        lists_loaded_at = time.time()
        config_reload_at = time.time()

        plates_counter = {}
        plates_score = {}
        plates_image = {}
        first_detect_time = None
        last_detect_time = None
        last_valid_plate = None
        last_valid_at = None

        while not stop_event.is_set():
            now = time.time()

            # Pick up config edits from the UI without needing a restart.
            # If URL or mode changed, return so _worker_loop recreates Capture.
            if now - config_reload_at > 5:
                fresh = AnprCameraModel.get_by_id(camera_id)
                if (not fresh or not fresh['feed_enabled']
                        or fresh['feed_mode'] != self.mode):
                    return
                if fresh['rtsp_url'] != initial_url:
                    logger.info(
                        "RTSP: camera %s URL changed, restarting capture",
                        camera_id)
                    return
                cam = dict(fresh)
                min_read = float(cam['min_read_score'] or 0.8)
                min_ratio = float(cam['min_ratio_score'] or 0.85)
                max_first = int(cam['max_first_detect_seconds'] or 3)
                max_last = int(cam['max_last_detect_seconds'] or 5)
                max_valid = int(cam['max_valid_detect_seconds'] or 10)
                region = self._parse_region(cam['detect_region'])
                config_reload_at = now

            if now - lists_loaded_at > WHITELIST_REFRESH_SECONDS:
                whitelist, blacklist = self._load_lists()
                lists_loaded_at = now

            if cap.error:
                AnprCameraModel.record_poll_error(camera_id, f'capture: {cap.error}')
                time.sleep(2)
                continue

            frame = cap.read()
            if frame is None:
                time.sleep(0.1)
                continue
            cap.clear()
            # Expose the latest frame so the UI preview endpoint can reuse
            # this open RTSP session instead of re-opening one (saves 2-5s).
            self._latest_frame[camera_id] = frame

            # Crop to detect region if configured
            if region is not None:
                x1, y1, x2, y2 = region
                h, w = frame.shape[:2]
                x1, x2 = max(0, x1), min(w, x2)
                y1, y2 = max(0, y1), min(h, y2)
                if x2 <= x1 or y2 <= y1:
                    crop = frame
                else:
                    crop = frame[y1:y2, x1:x2]
            else:
                crop = frame

            # Run OCR — encode crop as JPEG bytes so it reuses lpr_service.
            # Keep detector threshold low so we don't drop plates before OCR;
            # filter on the combined det*ocr score below using min_read_score.
            ok, buf = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                continue
            result = lpr_service.analyze(buf.tobytes(), min_confidence=0.25)
            if not result:
                self._maybe_finalize(
                    plates_counter, plates_score, plates_image,
                    first_detect_time, last_detect_time,
                    max_first, max_last,
                    cam, camera_id,
                )
                first_detect_time, last_detect_time, plates_counter, plates_score, plates_image = \
                    self._maybe_reset_window(
                        first_detect_time, last_detect_time,
                        max_first, max_last,
                        plates_counter, plates_score, plates_image,
                    )
                time.sleep(0.05)
                continue

            raw_plate = result['plate']
            plate_number = re.sub(r'[^A-Z0-9]', '', raw_plate.upper())
            plate_score = float(result['confidence'])
            logger.info("RTSP %s candidate: raw=%r norm=%s score=%.2f",
                        cam.get('name'), raw_plate, plate_number, plate_score)
            if plate_score < min_read:
                logger.info("  -> below min_read_score %.2f, skip", min_read)
                continue
            if not PLATE_REGEX.match(plate_number):
                logger.info("  -> regex reject")
                continue

            # Bbox is in coords of the image bytes we passed in (the crop),
            # so offset by the detect-region origin to get full-frame coords.
            bbox = result.get('bbox')
            if bbox and region is not None:
                bbox = [bbox[0] + region[0], bbox[1] + region[1],
                        bbox[2] + region[0], bbox[3] + region[1]]

            if first_detect_time is None:
                first_detect_time = time.time()
            last_detect_time = time.time()

            # Was this same plate validated very recently? skip
            if last_valid_plate == plate_number and last_valid_at \
                    and time.time() - last_valid_at < max_valid:
                continue

            is_valid, matched = self._validate(plate_number, whitelist, blacklist, min_ratio)
            if is_valid:
                last_valid_plate = matched
                last_valid_at = time.time()
                self._emit(cam, matched, plate_score, frame, bbox)
                plates_counter.clear()
                plates_score.clear()
                plates_image.clear()
                first_detect_time = None
                last_detect_time = None
                continue

            # Blacklisted match → emit and skip scoring window
            if matched is not None:
                last_valid_plate = matched
                last_valid_at = time.time()
                self._emit(cam, matched, plate_score, frame, bbox)
                plates_counter.clear()
                plates_score.clear()
                plates_image.clear()
                first_detect_time = None
                last_detect_time = None
                continue

            # Unknown plate — accumulate into scoring window
            plates_counter[plate_number] = plates_counter.get(plate_number, 0) + 1
            if plate_number not in plates_score or plate_score > plates_score[plate_number]:
                plates_score[plate_number] = plate_score
                plates_image[plate_number] = (frame.copy(), bbox)

            # Finalize scoring window if expired
            finalized = self._maybe_finalize(
                plates_counter, plates_score, plates_image,
                first_detect_time, last_detect_time,
                max_first, max_last,
                cam, camera_id,
            )
            if finalized:
                plates_counter.clear()
                plates_score.clear()
                plates_image.clear()
                first_detect_time = None
                last_detect_time = None

    def _maybe_reset_window(self, first_t, last_t, max_first, max_last,
                            counter, score, image):
        now = time.time()
        if first_t is not None and now - first_t > max_first:
            return None, None, {}, {}, {}
        if last_t is not None and now - last_t > max_last:
            return None, None, {}, {}, {}
        return first_t, last_t, counter, score, image

    def _maybe_finalize(self, counter, score, image,
                        first_t, last_t, max_first, max_last,
                        cam, camera_id):
        """If scoring window has expired, emit the best plate. Returns True if emitted."""
        if not counter:
            return False
        now = time.time()
        expired = False
        if first_t is not None and now - first_t > max_first:
            expired = True
        if last_t is not None and now - last_t > max_last:
            expired = True
        if not expired:
            return False

        best, best_count = None, 0
        for p, c in counter.items():
            weight = c + len(p)
            if weight > best_count:
                best, best_count = p, weight
        if best is None:
            return False

        # image[best] is a (frame, bbox) tuple from the scoring window
        frame_best, bbox_best = image[best]
        self._emit(cam, best, score[best], frame_best, bbox_best)
        return True

    def _emit(self, cam, plate, score, frame, bbox=None):
        """Send a confirmed plate through the access pipeline.

        Produces TWO images: a tight plate crop (using the detector bbox)
        and the full scene. If we don't have a bbox we fall back to one
        image — the full frame.
        """
        from database.models import AnprCameraModel
        from services.access_service import access_service
        import cv2

        camera_id = cam['id']
        # Dedupe same-plate within this window. Use per-camera config so it's
        # tunable from the UI. Slide the window forward on every same-plate
        # sighting, so a car sitting at the gate stays muted until it leaves.
        cooldown = int(cam.get('max_valid_detect_seconds') or 10)
        prev = self._last_plate.get(camera_id)
        now = datetime.now()
        if prev and prev[0] == plate and (now - prev[1]).total_seconds() < cooldown:
            self._last_plate[camera_id] = (plate, now)  # slide window
            logger.debug("RTSP %s suppressing duplicate %s (cooldown %ds)",
                         cam.get('name'), plate, cooldown)
            return
        self._last_plate[camera_id] = (plate, now)

        # Encode full scene as vehicle image
        ok, buf_full = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        vehicle_bytes = buf_full.tobytes() if ok else b''

        # Crop plate using bbox (+ small padding) if available
        plate_bytes = b''
        if bbox and len(bbox) == 4:
            h, w = frame.shape[:2]
            pad = 10
            x1 = max(0, int(bbox[0]) - pad)
            y1 = max(0, int(bbox[1]) - pad)
            x2 = min(w, int(bbox[2]) + pad)
            y2 = min(h, int(bbox[3]) + pad)
            if x2 > x1 and y2 > y1:
                crop = frame[y1:y2, x1:x2]
                ok2, buf_crop = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok2:
                    plate_bytes = buf_crop.tobytes()

        plate_images = []
        if plate_bytes:
            plate_images.append({'filename': 'plate.jpg', 'data': plate_bytes})
        elif vehicle_bytes:
            # No bbox → fall back to using the full frame as the plate image
            plate_images.append({'filename': 'plate.jpg', 'data': vehicle_bytes})
        vehicle_images = []
        if vehicle_bytes and plate_bytes:
            vehicle_images.append({'filename': 'vehicle.jpg', 'data': vehicle_bytes})

        AnprCameraModel.record_capture(camera_id, plate)
        AnprCameraModel.update_heartbeat_by_id(camera_id)

        logger.info("RTSP ANPR: camera=%s plate=%s score=%.2f (plate_img=%dB scene_img=%dB)",
                    cam.get('name'), plate, score,
                    len(plate_bytes), len(vehicle_bytes))

        access_service.process_vehicle(
            plate=plate,
            camera_ip=self._host_of(cam['rtsp_url']),
            plate_images=plate_images,
            vehicle_images=vehicle_images,
            location_id=cam['location_id'],
            camera_name=cam['name'],
            reg_code=cam['reg_code'],
        )

    @staticmethod
    def _parse_region(region):
        if not region:
            return None
        parts = region.replace(',', ' ').split()
        if len(parts) != 4:
            return None
        try:
            return [int(float(p)) for p in parts]
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _host_of(url):
        if not url:
            return ''
        try:
            from urllib.parse import urlparse
            return urlparse(url).hostname or ''
        except Exception:
            return ''
