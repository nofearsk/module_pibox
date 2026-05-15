"""
Open-source ANPR engine — CPU-only.

Pipeline:
    frame bytes
        -> decode (OpenCV)
        -> LicensePlateDetector (open-image-models, YOLO ONNX)
        -> crop per detection
        -> LicensePlateRecognizer (fast-plate-ocr, ONNX)
        -> best (plate, confidence)

Both models are ONNX Runtime on CPU — no GPU, no native compile step,
works on x86_64 and aarch64 (so Raspberry Pi is OK too). Models are
downloaded on first use and cached under `~/.cache/`.
"""
import logging
import threading

logger = logging.getLogger(__name__)


DEFAULT_DETECTOR_MODEL = 'yolo-v9-t-384-license-plate-end2end'
# fast-plate-ocr 2.x hub id. The 1.x fallback uses a different name
# (see _load_recognizer below).
DEFAULT_OCR_MODEL = 'cct-xs-v1-global-model'
DEFAULT_OCR_MODEL_V1 = 'global-plates-mobile-vit-v2-model'


class LprService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, '_initialized', False):
            return
        self._initialized = True
        self._detector = None
        self._recognizer = None
        self._recognizer_api = None
        self._lock = threading.Lock()
        self._status = {
            'enabled': False,
            'loaded': False,
            'detector_model': None,
            'ocr_model': None,
            'error': None,
        }

    # ---------- lifecycle ----------

    def init_engine(self):
        """Load detector + recognizer. Safe to call multiple times."""
        with self._lock:
            self._detector = None
            self._recognizer = None
            self._status.update({
                'enabled': False,
                'loaded': False,
                'error': None,
            })

            from config import config
            if config.get('lpr_enabled', 'false').lower() != 'true':
                self._status['error'] = 'disabled'
                return False

            try:
                import cv2  # noqa: F401
                from open_image_models import LicensePlateDetector
            except ImportError as e:
                logger.error("LPR detector dependency missing: %s", e)
                self._status['error'] = f'missing dependency: {e}'
                return False

            detector_model = config.get('lpr_detector_model', DEFAULT_DETECTOR_MODEL)

            try:
                logger.info("Loading LPR detector (%s)...", detector_model)
                self._detector = LicensePlateDetector(detection_model=detector_model)
            except Exception as e:
                logger.error("LPR detector init failed: %s", e, exc_info=True)
                self._status['error'] = str(e)
                return False

            try:
                ocr_model_used = self._load_recognizer(config)
            except Exception as e:
                logger.error("LPR recognizer init failed: %s", e, exc_info=True)
                self._status['error'] = str(e)
                return False

            self._status.update({
                'enabled': True,
                'loaded': True,
                'detector_model': detector_model,
                'ocr_model': ocr_model_used,
            })
            logger.info("LPR engine ready: detector=%s ocr=%s",
                        detector_model, ocr_model_used)
            return True

    def _load_recognizer(self, config):
        """
        Load the plate OCR recognizer. Supports both fast-plate-ocr APIs:
          - 2.x: `LicensePlateRecognizer(hub_ocr_model=...)`
          - 1.x: `ONNXPlateRecognizer(<model_name>)`

        Returns the model name/id that was loaded.
        """
        # Try 2.x API first
        try:
            from fast_plate_ocr import LicensePlateRecognizer
            model = config.get('lpr_ocr_model', DEFAULT_OCR_MODEL)
            logger.info("Loading LPR recognizer v2 (%s)...", model)
            self._recognizer = LicensePlateRecognizer(hub_ocr_model=model)
            self._recognizer_api = 2
            return model
        except ImportError:
            pass
        except Exception as e:
            logger.warning("v2 recognizer init failed (%s), falling back to v1", e)

        # Fall back to 1.x API
        from fast_plate_ocr import ONNXPlateRecognizer
        model = config.get('lpr_ocr_model_v1', DEFAULT_OCR_MODEL_V1)
        logger.info("Loading LPR recognizer v1 (%s)...", model)
        self._recognizer = ONNXPlateRecognizer(model)
        self._recognizer_api = 1
        return model

    def get_status(self):
        return dict(self._status)

    # ---------- inference ----------

    def analyze(self, image_bytes, min_confidence=0.5):
        """
        Detect + OCR a single frame.

        Args:
            image_bytes: JPEG/PNG bytes.
            min_confidence: reject detections with detection confidence below this.

        Returns:
            {'plate': 'ABC1234', 'confidence': 0.92, 'country': None} or None.
        """
        if not self._detector or not self._recognizer or not image_bytes:
            return None

        try:
            import cv2
            import numpy as np
            arr = np.frombuffer(image_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                return None
        except Exception as e:
            logger.debug("image decode failed: %s", e)
            return None

        try:
            detections = self._detector.predict(frame)
        except Exception as e:
            logger.debug("detector.predict failed: %s", e)
            return None

        best = None
        for det in detections or []:
            det_conf = _attr(det, 'confidence', 0.0)
            if det_conf < min_confidence:
                continue
            bbox = _attr(det, 'bounding_box', None)
            if bbox is None:
                continue
            x1 = max(0, int(_attr(bbox, 'x1', 0)))
            y1 = max(0, int(_attr(bbox, 'y1', 0)))
            x2 = int(_attr(bbox, 'x2', 0))
            y2 = int(_attr(bbox, 'y2', 0))
            if x2 <= x1 or y2 <= y1:
                continue
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            try:
                text, ocr_conf = self._recognize(crop)
            except Exception as e:
                logger.debug("OCR failed: %s", e)
                continue
            if not text:
                continue
            # Combined score = detection confidence * OCR mean confidence
            combined = float(det_conf) * float(ocr_conf) if ocr_conf else float(det_conf)
            if best is None or combined > best['confidence']:
                best = {
                    'plate': text.upper().strip(),
                    'confidence': combined,
                    'country': None,
                    'bbox': [x1, y1, x2, y2],  # in coords of the image bytes passed in
                }
        return best

    def _recognize(self, crop):
        """Run OCR on a single cropped plate (BGR numpy). Returns (text, confidence)."""
        # Try return_confidence first (both 1.x and 2.x support it, but
        # 1.x needs a 2-D grayscale array for some models). If that fails
        # we fall through to plain run().
        try:
            res = self._recognizer.run(crop, return_confidence=True)
            if isinstance(res, tuple) and len(res) == 2:
                plates, confs = res
                if plates:
                    text = plates[0] if isinstance(plates, list) else plates
                    conf = _mean(confs[0]) if (confs is not None and len(confs)) else 1.0
                    return _plate_text(text), conf
        except TypeError:
            pass  # no return_confidence kwarg on this version
        except Exception as e:
            logger.debug("OCR with return_confidence failed: %s", e)

        try:
            plates = self._recognizer.run(crop)
        except Exception as e:
            logger.debug("OCR run() failed: %s", e)
            return None, 0.0

        if isinstance(plates, (list, tuple)) and plates:
            return _plate_text(plates[0]), 1.0
        if isinstance(plates, str):
            return plates.strip(), 1.0
        return _plate_text(plates), 1.0


def _plate_text(obj):
    """Coerce fast-plate-ocr 1.x PlatePrediction (or any object) to plate string."""
    if obj is None:
        return None
    # fast-plate-ocr >=1.1 returns a PlatePrediction dataclass with .plate
    for attr in ('plate', 'text'):
        val = getattr(obj, attr, None)
        if isinstance(val, str) and val:
            return val.strip()
    if isinstance(obj, dict):
        for k in ('plate', 'text'):
            if isinstance(obj.get(k), str) and obj[k]:
                return obj[k].strip()
    return str(obj).strip()


def _attr(obj, name, default):
    """Safely read attribute-or-key from a detection result."""
    if obj is None:
        return default
    val = getattr(obj, name, None)
    if val is None and isinstance(obj, dict):
        val = obj.get(name, default)
    return default if val is None else val


def _mean(seq):
    try:
        import numpy as np
        a = np.asarray(seq, dtype=float)
        if a.size == 0:
            return 0.0
        return float(a.mean())
    except Exception:
        try:
            return float(sum(seq) / len(seq))
        except Exception:
            return 0.0


lpr_service = LprService()
