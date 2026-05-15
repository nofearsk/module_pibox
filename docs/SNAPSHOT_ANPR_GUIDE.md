# Snapshot ANPR (open-source, CPU-only)

PiBox can pull a snapshot URL from any IP camera on a timer and run plate
recognition **locally** using two open-source ONNX models, then route
the result through the existing PiBox pipeline (barrier, access log, Odoo
sync, WebSocket broadcast).

Use this when a camera doesn't push ANPR events, or when you want to use
a generic IP camera (Hikvision/Dahua snapshot endpoint, RTSP-to-HTTP
gateway, etc.) as an ANPR source.

## Engine

| Role | Library | License | Model |
|------|---------|---------|-------|
| Plate detection | [open-image-models](https://github.com/ankandrew/open-image-models) | MIT | `yolo-v9-t-384-license-plate-end2end` |
| Plate OCR | [fast-plate-ocr](https://github.com/ankandrew/fast-plate-ocr) | MIT | `cct-xs-v1-global-model` |

Both run on ONNX Runtime (CPU). No GPU required. Works on **x86_64 and
aarch64** — Raspberry Pi 4/5 are fine.

Models are downloaded and cached on first enable (~40 MB total), under
`~/.cache/`.

## Install

```bash
cd /odoo/custom/addons/module_pibox
pip3 install -r requirements.txt --break-system-packages
# or pip install in a venv
```

Restart PiBox. Visit `http://<pibox>:8080/snapshot-cameras` and click
**Enable** — first click loads the models (takes 10-30s the first time,
instant afterwards).

## Configure a camera

ANPR cameras come from the Odoo sync into the `anpr_cameras` table — the
same records used by push-mode (`reg_code`, `location_id`, relay
channels). This page adds snapshot config on top:

| Field | Meaning |
|-------|---------|
| Snapshot URL | Full URL returning a JPEG. Supports `http://user:pass@host/path`. |
| Poll interval | Seconds between fetches (1+). |
| Min confidence | Reject detections with confidence below this (0-1). 0.5 is a good starting point. |
| Enable | Master toggle per camera. |

Common snapshot URLs:
- Hikvision: `http://<user>:<pass>@<ip>/ISAPI/Streaming/channels/101/picture`
- Dahua: `http://<user>:<pass>@<ip>/cgi-bin/snapshot.cgi?channel=1`
- Axis: `http://<user>:<pass>@<ip>/axis-cgi/jpg/image.cgi`

Click **Test snapshot** to fetch one frame, run the pipeline, and see
the detected plate + preview without triggering the barrier.

## How it works

```
┌────────────┐  HTTP GET  ┌──────────┐  bytes  ┌──────────┐  crop   ┌─────────┐
│ IP camera  │ ────────── │ poller   │ ──────> │ detector │ ──────> │  OCR    │
└────────────┘ (interval) │  thread  │         │ (ONNX)   │         │ (ONNX)  │
                          └────┬─────┘         └──────────┘         └────┬────┘
                               │                                          │ plate
                               │  access_service.process_vehicle()  <─────┘
                               ▼
                  (relay, log, Odoo sync, WS broadcast)
```

- Each enabled camera has its own worker thread. Edits to interval /
  URL / enabled take effect within ~30 seconds without restart.
- Same plate from same camera within 8 seconds is debounced (treated as
  one vehicle, not repeated reads).
- On detection, the pipeline runs the **exact same path** as a
  Hikvision push event — so relay channels, access logs, and Odoo sync
  all "just work".

## Performance

Typical throughput on CPU (1080p snapshots, one camera):
- x86 (Intel i5 / Ryzen 5): 20-40 fps, so 2s poll interval uses <5% CPU
- Raspberry Pi 4: ~2-5 fps — keep polling interval ≥2s
- Raspberry Pi 5: ~8-15 fps

If you have many cameras, stagger intervals (3-5s) or upgrade the host.

## Troubleshooting

- **`missing dependency` in status** — run `pip install -r requirements.txt`.
- **First enable times out** — model download. Run PiBox with internet access
  on first boot, or pre-populate `~/.cache/`.
- **No plate detected** — lower `min_confidence`, verify image shows a
  clearly-visible plate (models need ~20px plate height minimum).
- **Repeated plates flood the log** — bump `DUPLICATE_WINDOW_SECONDS` in
  `services/snapshot_poller.py`.

## Files

- `services/lpr_service.py` — detector + OCR wrapper
- `services/snapshot_poller.py` — background polling workers
- `routes/api_routes.py` — `/api/lpr/*`, `/api/snapshot-cameras/*`
- `templates/snapshot_cameras.html` — UI page at `/snapshot-cameras`
