"""
Per-mode ANPR backends.

Each backend owns the workers for cameras whose `feed_mode` matches its
`mode` attribute. Backends expose:

    mode : str
    start_camera(cam_dict)
    stop_camera(camera_id)
    active_cameras() -> iterable[int]
    get_status() -> dict
    stop_all()

The AnprManager is the only thing that talks to backends; callers go
through the manager.
"""
