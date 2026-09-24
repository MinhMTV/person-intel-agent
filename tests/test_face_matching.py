import numpy as np

from app.domain.evidence import FaceMatchBand
from app.domain.image import BoundingBox, DetectedFace
from app.services.face_matching_service import FaceMatchingService, ReferenceFace
from tests.conftest import make_settings


def unit(cos: float) -> np.ndarray:
    return np.array([cos, np.sqrt(1 - cos * cos), 0.0])


def service(tmp_path) -> FaceMatchingService:
    return FaceMatchingService(make_settings(tmp_path), backend=None)


def test_band_mapping_uses_configured_thresholds(tmp_path):
    s = service(tmp_path)
    assert s.band_for(0.10) == FaceMatchBand.VERY_HIGH
    assert s.band_for(0.40) == FaceMatchBand.VERY_HIGH
    assert s.band_for(0.50) == FaceMatchBand.HIGH
    assert s.band_for(0.68) == FaceMatchBand.MEDIUM
    assert s.band_for(0.75) == FaceMatchBand.LOW
    assert s.band_for(0.95) == FaceMatchBand.NO_MATCH
    strict = FaceMatchingService(make_settings(tmp_path, face_high_threshold=0.3), backend=None)
    assert strict.band_for(0.5) == FaceMatchBand.MEDIUM


def test_compare_keeps_raw_measurements(tmp_path):
    cmp = service(tmp_path).compare(unit(1.0), unit(0.6))
    assert abs(cmp.cosine_similarity - 0.6) < 1e-3
    assert abs(cmp.distance - 0.4) < 1e-3
    assert cmp.band == FaceMatchBand.VERY_HIGH


def test_multiple_references_use_second_best_with_three_or_more(tmp_path):
    s = service(tmp_path)
    refs = [ReferenceFace("r1", "f1", unit(1.0)), ReferenceFace("r2", "f1", unit(0.2)), ReferenceFace("r3", "f1", unit(0.1))]
    cmp, best = s.compare_multiple_references(refs, unit(1.0))
    assert best.reference_image_id == "r1"
    assert cmp.references_compared == 3
    # one lucky reference is not enough: the second-closest (cos 0.2) decides the band
    assert cmp.band in (FaceMatchBand.NO_MATCH, FaceMatchBand.LOW)
    cmp2, _ = s.compare_multiple_references(refs[:2], unit(1.0))
    assert cmp2.band == FaceMatchBand.VERY_HIGH


def test_best_face_is_not_simply_the_largest(tmp_path):
    s = service(tmp_path)
    big = DetectedFace(id="big", bbox=BoundingBox(x=0, y=0, w=300, h=300), embedding=list(unit(0.1)))
    small = DetectedFace(id="small", bbox=BoundingBox(x=0, y=0, w=40, h=40), embedding=list(unit(0.95)))
    match = s.find_best_face_match([ReferenceFace("r", "f", unit(1.0))], [big, small])
    assert match.face.id == "small"
