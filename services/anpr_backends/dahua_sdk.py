"""
Dahua NetSDK backend — STUB.

To be implemented in Phase 2. Plan:

    Drop SDK into `vendor/dahua_netsdk/` (libdhnetsdk.so + deps).
    ctypes wrapper around:
        CLIENT_Init, CLIENT_Cleanup
        CLIENT_LoginWithHighLevelSecurity  → handle
        CLIENT_RealLoadPictureEx(EVENT_IVS_TRAFFICJUNCTION, callback)
        CLIENT_StopLoadPic, CLIENT_Logout
    Callback parses NET_DEV_EVENT_TRAFFICJUNCTION_INFO → (plate, image)
    Reconnect loop on disconnect.

Today this backend only records the camera as 'pending_sdk' so the UI
shows the intent; no events arrive yet.
"""
import logging
import threading

from .base import BaseBackend

logger = logging.getLogger(__name__)


class DahuaSdkBackend(BaseBackend):
    mode = 'dahua_sdk'

    def __init__(self):
        self._pending = {}  # camera_id -> cam dict
        self._lock = threading.Lock()
        self._warned_missing = False

    def active_cameras(self):
        return list(self._pending.keys())

    def start_camera(self, cam):
        with self._lock:
            self._pending[cam['id']] = dict(cam)
        if not self._warned_missing:
            logger.warning(
                "Dahua SDK backend not yet implemented — camera '%s' (id=%s) "
                "is marked pending. Install the Dahua NetSDK and implement "
                "the ctypes wrapper to activate.",
                cam.get('name'), cam['id'])
            self._warned_missing = True

    def stop_camera(self, camera_id):
        with self._lock:
            self._pending.pop(camera_id, None)

    def get_status(self):
        return {
            'mode': self.mode,
            'active': list(self._pending.keys()),
            'implemented': False,
            'note': 'Drop Dahua NetSDK into vendor/dahua_netsdk/ then enable.',
        }
