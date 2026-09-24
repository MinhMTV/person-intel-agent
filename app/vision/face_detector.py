"""Face detection primitives and the backend protocol.

A *face backend* detects faces in an RGB image and (optionally) computes an
identity embedding for each face. The OpenCV Haar backend here only detects
faces — it lets the UI show faces / quality even when no embedding model is
installed, but it cannot be used for identity matching.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass
class FaceObservation:
    x: int
    y: int
    w: int
    h: int
    confidence: float | None = None
    embedding: np.ndarray | None = None
    left_eye: tuple[int, int] | None = None
    right_eye: tuple[int, int] | None = None


class FaceBackend(Protocol):
    name: str
    model: str | None  # None → detection only, no identity embeddings

    @property
    def supports_embeddings(self) -> bool: ...

    def analyze(self, rgb: np.ndarray) -> list[FaceObservation]:
        """Detect faces (+ embeddings when supported). Must be thread-safe enough
        to be called from a worker thread; callers bound the concurrency."""
        ...


class HaarFaceDetector:
    """Detection-only fallback using OpenCV's bundled Haar cascade."""

    name = "opencv-haar"
    model: str | None = None

    def __init__(self) -> None:
        import cv2

        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"  # type: ignore[attr-defined]
        self._cascade = cv2.CascadeClassifier(path)
        if self._cascade.empty():  # pragma: no cover - broken OpenCV install
            raise RuntimeError(f"Could not load Haar cascade at {path}")

    @property
    def supports_embeddings(self) -> bool:
        return False

    def analyze(self, rgb: np.ndarray) -> list[FaceObservation]:
        import cv2

        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        min_side = max(24, int(min(gray.shape[:2]) * 0.04))
        faces = self._cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(min_side, min_side))
        return [FaceObservation(int(x), int(y), int(w), int(h), None) for (x, y, w, h) in faces]
