"""
Backward-compatibility shim.

The snapshot polling logic moved to `services.anpr_backends.snapshot`
and is now coordinated by `services.anpr_manager.anpr_manager`. Older
code that imported `snapshot_poller` from here keeps working via this
adapter.
"""
from services.anpr_manager import anpr_manager
from services.anpr_backends.snapshot import _fetch_snapshot  # noqa: F401


class _SnapshotPollerCompat:
    """Minimal surface matching the old class so existing callers still work."""

    def start(self):
        anpr_manager.start()

    def stop(self):
        anpr_manager.stop()

    def get_status(self):
        status = anpr_manager.get_status()
        snap = status['backends'].get('snapshot', {})
        workers = snap.get('active', [])
        return {
            'running': status['running'],
            'workers': workers,
            'worker_count': len(workers),
        }


snapshot_poller = _SnapshotPollerCompat()
