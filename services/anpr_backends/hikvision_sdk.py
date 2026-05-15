"""
Hikvision HCNetSDK backend — STUB.

To be implemented in Phase 2. Plan:

    Drop SDK into `vendor/hikvision_hcnetsdk/` (libhcnetsdk.so + 30 deps).
    ctypes wrapper around:
        NET_DVR_Init, NET_DVR_Cleanup
        NET_DVR_Login_V40 → user_id
        NET_DVR_SetDVRMessageCallBack_V50(callback)
        NET_DVR_SetupAlarmChan_V41
    Callback filters COMM_ITS_PLATE_RESULT → NET_ITS_PLATE_RESULT struct
    Parse plate + image, reconnect on link drop.

Today this backend only records the camera as 'pending_sdk' so the UI
shows the intent; no events arrive yet.
"""
import logging
import threading

from .base import BaseBackend

logger = logging.getLogger(__name__)


class HikvisionSdkBackend(BaseBackend):
    mode = 'hikvision_sdk'

    def __init__(self):
        self._pending = {}
        self._lock = threading.Lock()
        self._warned_missing = False

    def active_cameras(self):
        return list(self._pending.keys())

    def start_camera(self, cam):
        with self._lock:
            self._pending[cam['id']] = dict(cam)
        if not self._warned_missing:
            logger.warning(
                "Hikvision SDK backend not yet implemented — camera '%s' (id=%s) "
                "is marked pending. Install the HCNetSDK and implement the "
                "ctypes wrapper to activate.",
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
            'note': 'Drop Hikvision HCNetSDK into vendor/hikvision_hcnetsdk/ then enable.',
        }
