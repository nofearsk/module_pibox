"""
ANPR manager.

Owns a set of per-mode backends and runs a reconciliation loop that
picks up camera-config changes every 30s. For each camera with
`feed_enabled=1` it ensures the correct backend is running a worker;
for cameras no longer wanted it stops the worker.

`http_push` cameras are not managed — camera → PiBox HTTP endpoints
handle those. The manager only tracks them for UI stats.
"""
import logging
import threading
import time

from services.anpr_backends.snapshot import SnapshotBackend
from services.anpr_backends.dahua_sdk import DahuaSdkBackend
from services.anpr_backends.hikvision_sdk import HikvisionSdkBackend
from services.anpr_backends.rtsp import RtspBackend

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL_SECONDS = 30


class AnprManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, '_initialized', False):
            return
        self._initialized = True
        self._running = False
        self._thread = None
        self.backends = {
            SnapshotBackend.mode: SnapshotBackend(),
            DahuaSdkBackend.mode: DahuaSdkBackend(),
            HikvisionSdkBackend.mode: HikvisionSdkBackend(),
            RtspBackend.mode: RtspBackend(),
        }

    # ---------- lifecycle ----------

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name='AnprManager', daemon=True
        )
        self._thread.start()
        logger.info("AnprManager started (backends: %s)", list(self.backends.keys()))

    def stop(self):
        self._running = False
        for backend in self.backends.values():
            try:
                backend.stop_all()
            except Exception as e:
                logger.warning("backend.stop_all failed: %s", e)
        logger.info("AnprManager stopped")

    def get_status(self):
        return {
            'running': self._running,
            'backends': {m: b.get_status() for m, b in self.backends.items()},
        }

    # ---------- reconciliation ----------

    def _loop(self):
        while self._running:
            try:
                self.reconcile()
            except Exception as e:
                logger.error("reconcile error: %s", e, exc_info=True)
            for _ in range(RECONCILE_INTERVAL_SECONDS):
                if not self._running:
                    return
                time.sleep(1)

    def reconcile(self):
        """One pass: align backend workers with current camera config."""
        from database.models import AnprCameraModel

        desired = {c['id']: dict(c)
                   for c in AnprCameraModel.get_feed_enabled()
                   if c['feed_mode'] in self.backends}
        current = {}
        for backend in self.backends.values():
            for cid in backend.active_cameras():
                current[cid] = backend.mode

        # Stop cameras that are gone / moved to a different mode
        for cid, mode in current.items():
            if cid not in desired or desired[cid]['feed_mode'] != mode:
                self.backends[mode].stop_camera(cid)

        # Start / restart cameras
        for cid, cam in desired.items():
            backend = self.backends[cam['feed_mode']]
            if cid not in backend.active_cameras():
                try:
                    backend.start_camera(cam)
                except Exception as e:
                    logger.error("start_camera(%s) in %s failed: %s",
                                 cid, backend.mode, e, exc_info=True)


anpr_manager = AnprManager()
