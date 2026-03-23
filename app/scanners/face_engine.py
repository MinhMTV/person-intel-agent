"""Enhanced Face Recognition Engine — Multi-backend face analysis.

Supports two backends:
  - face_recognition (dlib): Fast, 128-d embeddings, good for general use
  - DeepFace (ArcFace/Facenet/VGG-Face): Higher accuracy, multiple models

Also provides:
  - Face quality scoring (blur, lighting, face angle)
  - Batch face comparison
  - Age/gender/emotion estimation (DeepFace)
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np


class FaceBackend(str, Enum):
    DLIB = "dlib"
    DEEPFACE = "deepface"


class DeepFaceModel(str, Enum):
    """Available DeepFace model backends."""
    VGG_FACE = "VGG-Face"
    FACENET = "Facenet"
    FACENET512 = "Facenet512"
    OPENFACE = "OpenFace"
    DEEPID = "DeepID"
    DLIB = "Dlib"
    ARC_FACE = "ArcFace"
    SFACE = "SFace"
    GHOSTFACENET = "GhostFaceNet"


@dataclass
class FaceQuality:
    """Quality assessment of a face image."""
    blur_score: float = 0.0        # 0-100 (higher = sharper)
    brightness: float = 0.0        # 0-255 mean pixel value
    contrast: float = 0.0          # std deviation of pixels
    face_angle: float = 0.0        # Estimated rotation angle in degrees
    face_size_ratio: float = 0.0   # Face bbox area / image area
    quality_grade: str = "unknown" # A/B/C/D/F
    is_usable: bool = False
    issues: list[str] = field(default_factory=list)

    @property
    def quality_score(self) -> float:
        """Overall quality score 0-100."""
        score = 0.0
        # Blur component (0-30)
        score += min(30, self.blur_score / 3.33)
        # Brightness component (0-25) - ideal is 100-180
        if 80 <= self.brightness <= 200:
            score += 25
        elif 50 <= self.brightness <= 220:
            score += 15
        else:
            score += 5
        # Contrast component (0-20)
        score += min(20, self.contrast / 3)
        # Face size component (0-15) - bigger face = better
        score += min(15, self.face_size_ratio * 100)
        # Angle component (0-10) - frontal is best
        angle_penalty = min(abs(self.face_angle), 45) / 45
        score += 10 * (1 - angle_penalty)
        return round(min(100, score), 1)


@dataclass
class FaceAnalysisResult:
    """Complete face analysis result."""
    face_detected: bool = False
    quality: Optional[FaceQuality] = None
    age_estimate: Optional[int] = None
    gender: Optional[str] = None
    emotion: Optional[str] = None
    embedding: Optional[np.ndarray] = None
    backend: str = "unknown"
    model: str = "unknown"


class FaceEngine:
    """Multi-backend face recognition and analysis engine.

    Usage:
        engine = FaceEngine(backend="deepface", model="ArcFace")
        result = engine.analyze("photo.jpg")
        match = engine.compare("face1.jpg", "face2.jpg")
    """

    DEFAULT_SIMILARITY_THRESHOLD = 0.6

    def __init__(
        self,
        backend: str = "deepface",
        model: str = "ArcFace",
        detector_backend: str = "retinaface",
    ):
        self.backend = FaceBackend(backend)
        self.model_name = model
        self.detector_backend = detector_backend
        self._deepface = None
        self._fr = None

    @property
    def deepface(self):
        if self._deepface is None:
            from deepface import DeepFace
            self._deepface = DeepFace
        return self._deepface

    @property
    def face_recognition(self):
        if self._fr is None:
            try:
                import face_recognition as fr
            except ImportError as e:
                raise RuntimeError(
                    "The dlib backend requires the optional 'face-recognition' package. "
                    "Install CMake first, then install 'face-recognition', or use '--backend deepface'."
                ) from e
            self._fr = fr
        return self._fr

    # =========================================================================
    # Face Quality Scoring
    # =========================================================================

    def assess_quality(self, image_path: str) -> FaceQuality:
        """Assess face image quality: blur, lighting, angle, size."""
        import cv2

        quality = FaceQuality()
        img = cv2.imread(image_path)
        if img is None:
            quality.issues.append("Could not read image")
            return quality

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        # 1. Blur detection (Laplacian variance)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        quality.blur_score = round(min(100, laplacian_var / 5), 1)
        if laplacian_var < 50:
            quality.issues.append(f"Image is blurry (Laplacian variance: {laplacian_var:.0f})")

        # 2. Brightness and contrast
        quality.brightness = round(float(np.mean(gray)), 1)
        quality.contrast = round(float(np.std(gray)), 1)
        if quality.brightness < 50:
            quality.issues.append("Image is too dark")
        elif quality.brightness > 220:
            quality.issues.append("Image is overexposed")
        if quality.contrast < 20:
            quality.issues.append("Low contrast")

        # 3. Face detection for size ratio and angle estimation
        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        faces = face_cascade.detectMultiScale(gray, 1.1, 4)

        if len(faces) > 0:
            # Use the largest face
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            quality.face_size_ratio = round((fw * fh) / (w * h), 3)

            if quality.face_size_ratio < 0.02:
                quality.issues.append("Face is very small in the image")

            # Estimate face angle using eye detection
            face_roi_gray = gray[fy:fy+fh, fx:fx+fw]
            eye_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_eye.xml"
            )
            eyes = eye_cascade.detectMultiScale(face_roi_gray)
            if len(eyes) >= 2:
                # Sort eyes by x position
                eyes = sorted(eyes, key=lambda e: e[0])
                ex1, ey1 = eyes[0][0] + eyes[0][2]//2, eyes[0][1] + eyes[0][3]//2
                ex2, ey2 = eyes[1][0] + eyes[1][2]//2, eyes[1][1] + eyes[1][3]//2
                import math
                angle = math.degrees(math.atan2(ey2 - ey1, ex2 - ex1))
                quality.face_angle = round(angle, 1)
                if abs(angle) > 15:
                    quality.issues.append(f"Head tilted {abs(angle):.0f} degrees")
        else:
            quality.issues.append("No face detected by OpenCV (may still work with deep learning detector)")

        # Grade
        score = quality.quality_score
        if score >= 80:
            quality.quality_grade = "A"
            quality.is_usable = True
        elif score >= 60:
            quality.quality_grade = "B"
            quality.is_usable = True
        elif score >= 40:
            quality.quality_grade = "C"
            quality.is_usable = True
        elif score >= 25:
            quality.quality_grade = "D"
            quality.is_usable = False
        else:
            quality.quality_grade = "F"
            quality.is_usable = False

        return quality

    # =========================================================================
    # Face Analysis (age, gender, emotion)
    # =========================================================================

    def analyze(self, image_path: str) -> FaceAnalysisResult:
        """Full face analysis: detection, quality, demographics, embedding."""
        result = FaceAnalysisResult(backend=self.backend.value, model=self.model_name)

        # Quality assessment
        result.quality = self.assess_quality(image_path)

        if self.backend == FaceBackend.DEEPFACE:
            return self._analyze_deepface(image_path, result)
        else:
            return self._analyze_dlib(image_path, result)

    def _analyze_deepface(self, image_path: str, result: FaceAnalysisResult) -> FaceAnalysisResult:
        """Analyze face using DeepFace."""
        try:
            DF = self.deepface

            # Demographics (age, gender, emotion)
            try:
                demography = DF.analyze(
                    img_path=image_path,
                    actions=["age", "gender", "emotion"],
                    detector_backend=self.detector_backend,
                    enforce_detection=False,
                    silent=True,
                )
                if demography and isinstance(demography, list):
                    demography = demography[0]
                result.face_detected = True
                result.age_estimate = demography.get("age")
                result.gender = demography.get("dominant_gender")
                result.emotion = demography.get("dominant_emotion")
            except Exception:
                pass

            # Embedding
            try:
                embeddings = DF.represent(
                    img_path=image_path,
                    model_name=self.model_name,
                    detector_backend=self.detector_backend,
                    enforce_detection=False,
                )
                if embeddings:
                    result.embedding = np.array(embeddings[0]["embedding"])
                    result.face_detected = True
            except Exception:
                pass

        except Exception as e:
            print(f"DeepFace analysis error: {e}")

        return result

    def _analyze_dlib(self, image_path: str, result: FaceAnalysisResult) -> FaceAnalysisResult:
        """Analyze face using dlib/face_recognition."""
        fr = self.face_recognition
        try:
            image = fr.load_image_file(image_path)
            encodings = fr.face_encodings(image)
            if encodings:
                result.face_detected = True
                result.embedding = encodings[0]
        except Exception as e:
            print(f"dlib analysis error: {e}")
        return result

    # =========================================================================
    # Face Comparison
    # =========================================================================

    def compare(
        self,
        image1_path: str,
        image2_path: str,
        threshold: Optional[float] = None,
    ) -> dict:
        """Compare two face images and return similarity score + match decision.

        Returns:
            dict with: similarity, is_match, distance, threshold, backend
        """
        if threshold is None:
            threshold = self.DEFAULT_SIMILARITY_THRESHOLD

        if self.backend == FaceBackend.DEEPFACE:
            return self._compare_deepface(image1_path, image2_path, threshold)
        else:
            return self._compare_dlib(image1_path, image2_path, threshold)

    def _compare_deepface(self, img1: str, img2: str, threshold: float) -> dict:
        """Compare using DeepFace."""
        try:
            DF = self.deepface
            result = DF.verify(
                img1_path=img1,
                img2_path=img2,
                model_name=self.model_name,
                detector_backend=self.detector_backend,
                enforce_detection=False,
                silent=True,
            )
            distance = result.get("distance", 1.0)
            # Convert distance to similarity (model-dependent)
            similarity = max(0, 1.0 - distance)
            return {
                "similarity": round(similarity, 4),
                "is_match": result.get("verified", False),
                "distance": round(distance, 4),
                "threshold": result.get("threshold", threshold),
                "backend": f"deepface/{self.model_name}",
                "model": self.model_name,
            }
        except Exception as e:
            return {"similarity": 0, "is_match": False, "error": str(e), "backend": "deepface"}

    def _compare_dlib(self, img1: str, img2: str, threshold: float) -> dict:
        """Compare using dlib/face_recognition."""
        fr = self.face_recognition
        try:
            image1 = fr.load_image_file(img1)
            enc1 = fr.face_encodings(image1)
            image2 = fr.load_image_file(img2)
            enc2 = fr.face_encodings(image2)

            if not enc1 or not enc2:
                return {"similarity": 0, "is_match": False, "error": "No face detected", "backend": "dlib"}

            distance = fr.face_distance([enc1[0]], enc2[0])[0]
            similarity = round(1.0 - float(distance), 4)
            return {
                "similarity": similarity,
                "is_match": float(distance) <= threshold,
                "distance": round(float(distance), 4),
                "threshold": threshold,
                "backend": "dlib/face_recognition",
                "model": "dlib_resnet",
            }
        except Exception as e:
            return {"similarity": 0, "is_match": False, "error": str(e), "backend": "dlib"}

    # =========================================================================
    # Batch Comparison
    # =========================================================================

    def batch_compare(
        self,
        reference_path: str,
        candidate_paths: list[str],
        threshold: Optional[float] = None,
    ) -> list[dict]:
        """Compare one reference face against multiple candidates.

        Returns list of dicts sorted by similarity (highest first).
        Each dict has: path, similarity, is_match, distance, quality_grade
        """
        if threshold is None:
            threshold = self.DEFAULT_SIMILARITY_THRESHOLD

        results = []
        for candidate in candidate_paths:
            try:
                comparison = self.compare(reference_path, candidate, threshold)
                quality = self.assess_quality(candidate)
                comparison["path"] = candidate
                comparison["quality_grade"] = quality.quality_grade
                comparison["quality_score"] = quality.quality_score
                results.append(comparison)
            except Exception as e:
                results.append({
                    "path": candidate,
                    "similarity": 0,
                    "is_match": False,
                    "error": str(e),
                })

        results.sort(key=lambda r: r.get("similarity", 0), reverse=True)
        return results

    # =========================================================================
    # Embedding Extraction (for database storage)
    # =========================================================================

    def get_embedding(self, image_path: str) -> Optional[np.ndarray]:
        """Extract face embedding vector from image."""
        result = self.analyze(image_path)
        return result.embedding

    def get_embedding_as_list(self, image_path: str) -> Optional[list[float]]:
        """Extract face embedding as a plain Python list (for JSON serialization)."""
        emb = self.get_embedding(image_path)
        if emb is not None:
            return emb.tolist()
        return None


# =============================================================================
# Convenience Functions
# =============================================================================

def create_engine(backend: str = "deepface", model: str = "ArcFace") -> FaceEngine:
    """Create a face engine with the specified backend."""
    return FaceEngine(backend=backend, model=model)


def quick_compare(img1: str, img2: str, backend: str = "deepface") -> dict:
    """Quick one-shot comparison of two images."""
    engine = create_engine(backend=backend)
    return engine.compare(img1, img2)


def quick_quality_check(image_path: str) -> FaceQuality:
    """Quick quality assessment of a face image."""
    engine = create_engine()
    return engine.assess_quality(image_path)
