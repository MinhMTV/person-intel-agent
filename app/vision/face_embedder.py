"""Identity embedding backend (DeepFace / ArcFace) and backend factory.

Only ONE primary embedding model is used for matching (``FACE_MODEL``,
default ArcFace). Demographic inference (age/gender/emotion) is not part of
the pipeline.
"""

from __future__ import annotations

import logging
import threading

import numpy as np

from app.config import Settings
from app.vision.face_detector import FaceBackend, FaceObservation, HaarFaceDetector

logger = logging.getLogger(__name__)


class DeepFaceBackend:
    """Detect + align + embed faces with DeepFace (default: ArcFace, 512-d)."""

    name = "deepface"

    def __init__(self, model: str = "ArcFace", detector: str = "opencv"):
        from deepface import DeepFace  # noqa: F401  (import check)

        self.model: str | None = model
        self.detector = detector
        self._lock = threading.Lock()  # TF graph construction is not thread-safe

    @property
    def supports_embeddings(self) -> bool:
        return True

    def analyze(self, rgb: np.ndarray) -> list[FaceObservation]:
        from deepface import DeepFace

        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        try:
            with self._lock:
                reps = DeepFace.represent(
                    img_path=bgr,
                    model_name=self.model,
                    detector_backend=self.detector,
                    enforce_detection=True,  # never embed a whole image as a "face"
                    align=True,
                )
        except ValueError as exc:  # DeepFace raises (a subclass of) ValueError when no face is found
            if "face could not be detected" in str(exc).lower() or type(exc).__name__ == "FaceNotDetected":
                return []
            raise
        faces: list[FaceObservation] = []
        for rep in reps:
            area = rep.get("facial_area") or {}
            confidence = rep.get("face_confidence")
            if confidence is not None and float(confidence) <= 0.0:
                continue  # DeepFace placeholder for "no detection"
            faces.append(
                FaceObservation(
                    x=int(area.get("x", 0)),
                    y=int(area.get("y", 0)),
                    w=int(area.get("w", 0)),
                    h=int(area.get("h", 0)),
                    confidence=float(confidence) if confidence is not None else None,
                    embedding=np.asarray(rep["embedding"], dtype=np.float32),
                    left_eye=_point(area.get("left_eye")),
                    right_eye=_point(area.get("right_eye")),
                )
            )
        return faces


def _point(value: object) -> tuple[int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), int(value[1])
    return None


def build_face_backend(settings: Settings) -> tuple[FaceBackend | None, str | None]:
    """Return ``(backend, warning)``.

    Falls back to detection-only (Haar) when DeepFace is not installed so the
    rest of the pipeline keeps working; identity matching is then disabled.
    """
    try:
        return DeepFaceBackend(model=settings.face_model, detector=settings.face_detector), None
    except Exception as exc:  # ImportError, TF init errors, …
        logger.warning("DeepFace unavailable (%s); face matching disabled", type(exc).__name__)
        warning = (
            "Face identity matching is unavailable (DeepFace/TensorFlow not installed). "
            "Only detection, image-occurrence and text evidence will be used."
        )
    try:
        return HaarFaceDetector(), warning
    except Exception:  # pragma: no cover
        return None, warning + " Face detection is unavailable too (OpenCV missing)."
