"""Image/face quality signals → GOOD / USABLE / POOR.

The label is advisory: searches are never blocked on quality, the user is
warned that matching may be unreliable.
"""

from __future__ import annotations

import math

import numpy as np

from app.domain.image import ImageQuality, QualityLabel
from app.vision.face_detector import FaceObservation


def laplacian_variance(gray: np.ndarray) -> float:
    """Sharpness measure (higher = sharper)."""
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    g = gray.astype(np.float64)
    lap = (
        -4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    )
    return float(lap.var())


def _gray(rgb: np.ndarray) -> np.ndarray:
    return (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]).astype(np.float64)


def face_region_stats(rgb: np.ndarray, face: FaceObservation) -> tuple[float, float, float | None]:
    """Return (blur_variance, brightness, roll_degrees) for one face box."""
    h, w = rgb.shape[:2]
    x0, y0 = max(0, face.x), max(0, face.y)
    x1, y1 = min(w, face.x + face.w), min(h, face.y + face.h)
    region = _gray(rgb[y0:y1, x0:x1]) if x1 > x0 and y1 > y0 else _gray(rgb)
    # Normalise scale so blur is comparable across face sizes.
    if region.shape[0] > 160:
        step = max(1, region.shape[0] // 160)
        region = region[::step, ::step]
    roll = None
    if face.left_eye and face.right_eye:
        (lx, ly), (rx, ry) = face.left_eye, face.right_eye
        dx, dy = lx - rx, ly - ry
        if dx:
            roll = round(math.degrees(math.atan2(dy, dx)), 1)
            if roll > 90:
                roll -= 180
            elif roll < -90:
                roll += 180
    return laplacian_variance(region), float(region.mean()), roll


def assess_quality(rgb: np.ndarray, faces: list[FaceObservation], target: FaceObservation | None) -> ImageQuality:
    h, w = rgb.shape[:2]
    issues: list[str] = []
    if not faces:
        return ImageQuality(
            label=QualityLabel.POOR, width=w, height=h, face_count=0,
            issues=["No face detected — face matching will not be possible; reverse image search still runs."],
        )
    target = target or max(faces, key=lambda f: f.w * f.h)
    blur, brightness, roll = face_region_stats(rgb, target)
    ratio = (target.w * target.h) / float(w * h)
    min_side = min(target.w, target.h)
    penalties = 0

    if min_side < 60:
        issues.append(f"Target face is very small ({min_side}px); matching is unreliable.")
        penalties += 2
    elif min_side < 112:
        issues.append(f"Target face is small ({min_side}px).")
        penalties += 1
    if blur < 30:
        issues.append("Face region is very blurry.")
        penalties += 2
    elif blur < 90:
        issues.append("Face region is somewhat blurry.")
        penalties += 1
    if brightness < 45:
        issues.append("Face region is very dark.")
        penalties += 1
    elif brightness > 225:
        issues.append("Face region is overexposed.")
        penalties += 1
    if roll is not None and abs(roll) > 25:
        issues.append(f"Head strongly tilted (~{abs(roll):.0f}°).")
        penalties += 1
    if len(faces) > 1:
        issues.append(f"{len(faces)} faces detected — confirm the selected target face.")
    if target.confidence is not None and target.confidence < 0.6:
        issues.append("Low face-detector confidence (possible occlusion or extreme pose).")
        penalties += 1

    label = QualityLabel.GOOD if penalties == 0 else QualityLabel.USABLE if penalties <= 2 else QualityLabel.POOR
    return ImageQuality(
        label=label,
        width=w,
        height=h,
        face_count=len(faces),
        face_box_ratio=round(ratio, 4),
        face_min_side=min_side,
        blur_variance=round(blur, 1),
        brightness=round(brightness, 1),
        roll_degrees=roll,
        issues=issues,
    )
