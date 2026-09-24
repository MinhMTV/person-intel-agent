"""DeepFaceBackend adapter logic, with the heavy `deepface` module mocked."""

import sys
import types

import numpy as np

from app.vision.face_embedder import DeepFaceBackend, build_face_backend
from tests.conftest import make_settings


class FaceNotDetected(ValueError):
    pass


def install_fake_deepface(monkeypatch, represent):
    module = types.ModuleType("deepface")
    module.DeepFace = types.SimpleNamespace(represent=represent)
    monkeypatch.setitem(sys.modules, "deepface", module)


def test_enforces_detection_and_filters_placeholder_faces(monkeypatch):
    calls = {}

    def represent(**kwargs):
        calls.update(kwargs)
        return [
            {
                "embedding": [1.0, 0.0],
                "facial_area": {"x": 5, "y": 6, "w": 40, "h": 50, "left_eye": (30, 20), "right_eye": (15, 21)},
                "face_confidence": 0.93,
            },
            {"embedding": [0.0, 1.0], "facial_area": {"x": 0, "y": 0, "w": 99, "h": 99}, "face_confidence": 0.0},
        ]

    install_fake_deepface(monkeypatch, represent)
    backend = DeepFaceBackend(model="ArcFace", detector="opencv")
    faces = backend.analyze(np.zeros((100, 100, 3), dtype=np.uint8))
    assert calls["enforce_detection"] is True and calls["model_name"] == "ArcFace"
    assert calls["img_path"].shape == (100, 100, 3)  # BGR numpy array, no temp files
    assert len(faces) == 1 and (faces[0].x, faces[0].w, faces[0].confidence) == (5, 40, 0.93)
    assert faces[0].left_eye == (30, 20)


def test_no_face_returns_empty(monkeypatch):
    def represent(**kwargs):
        raise FaceNotDetected("Face could not be detected in numpy array.")

    install_fake_deepface(monkeypatch, represent)
    assert DeepFaceBackend().analyze(np.zeros((10, 10, 3), dtype=np.uint8)) == []


def test_factory_falls_back_to_detection_only(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "deepface", None)  # import fails
    backend, warning = build_face_backend(make_settings(tmp_path))
    assert backend is not None and not backend.supports_embeddings
    assert "unavailable" in warning
