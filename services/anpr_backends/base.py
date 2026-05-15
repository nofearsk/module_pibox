"""
Backend base class — thin interface, each mode implements what it needs.
"""
import logging

logger = logging.getLogger(__name__)


class BaseBackend:
    mode = 'base'

    def start_camera(self, cam):
        raise NotImplementedError

    def stop_camera(self, camera_id):
        raise NotImplementedError

    def active_cameras(self):
        return []

    def get_status(self):
        return {'mode': self.mode, 'active': list(self.active_cameras())}

    def stop_all(self):
        for cid in list(self.active_cameras()):
            try:
                self.stop_camera(cid)
            except Exception as e:
                logger.warning("stop_camera(%s) failed: %s", cid, e)
