"""Tests for enhanced face recognition engine."""

import os
import sys
import json

# Ensure project root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.scanners.face_engine import FaceEngine, FaceBackend, quick_compare, quick_quality_check


TEST_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "test_data")


def test_quality_assessment():
    """Test face quality scoring on test images."""
    print("=" * 60)
    print("TEST: Face Quality Assessment")
    print("=" * 60)

    engine = FaceEngine(backend="deepface")

    for fname in os.listdir(TEST_DATA_DIR):
        if not fname.endswith((".jpg", ".jpeg", ".png")):
            continue
        path = os.path.join(TEST_DATA_DIR, fname)
        print(f"\n📸 Analyzing: {fname}")
        quality = engine.assess_quality(path)
        print(f"  Blur score:     {quality.blur_score}/100")
        print(f"  Brightness:     {quality.brightness}")
        print(f"  Contrast:       {quality.contrast}")
        print(f"  Face angle:     {quality.face_angle}°")
        print(f"  Face size ratio:{quality.face_size_ratio}")
        print(f"  Quality grade:  {quality.quality_grade}")
        print(f"  Overall score:  {quality.quality_score}/100")
        print(f"  Usable:         {quality.is_usable}")
        if quality.issues:
            print(f"  Issues:         {', '.join(quality.issues)}")


def test_face_analysis():
    """Test full face analysis with DeepFace."""
    print("\n" + "=" * 60)
    print("TEST: Face Analysis (DeepFace/ArcFace)")
    print("=" * 60)

    engine = FaceEngine(backend="deepface", model="ArcFace")

    ref_path = os.path.join(TEST_DATA_DIR, "konstantin_marbach_real.jpg")
    if not os.path.exists(ref_path):
        print(f"⚠️ Reference photo not found: {ref_path}")
        return

    result = engine.analyze(ref_path)
    print(f"\n📸 Reference: konstantin_marbach_real.jpg")
    print(f"  Face detected:  {result.face_detected}")
    print(f"  Backend:        {result.backend}/{result.model}")
    print(f"  Age estimate:   {result.age_estimate}")
    print(f"  Gender:         {result.gender}")
    print(f"  Emotion:        {result.emotion}")
    print(f"  Embedding dim:  {len(result.embedding) if result.embedding is not None else 'N/A'}")
    print(f"  Quality grade:  {result.quality.quality_grade if result.quality else 'N/A'}")


def test_face_comparison():
    """Test face comparison between images."""
    print("\n" + "=" * 60)
    print("TEST: Face Comparison (DeepFace/ArcFace)")
    print("=" * 60)

    engine = FaceEngine(backend="deepface", model="ArcFace")

    ref_path = os.path.join(TEST_DATA_DIR, "konstantin_marbach_real.jpg")
    if not os.path.exists(ref_path):
        print("⚠️ Reference photo not found")
        return

    # Compare reference with itself (should be a match)
    print("\n📸 Self-comparison test:")
    result = engine.compare(ref_path, ref_path)
    print(f"  Similarity: {result.get('similarity', 0):.4f}")
    print(f"  Is match:   {result.get('is_match', False)}")
    print(f"  Distance:   {result.get('distance', 'N/A')}")
    print(f"  Backend:    {result.get('backend', 'N/A')}")

    # Compare with all other images
    for fname in sorted(os.listdir(TEST_DATA_DIR)):
        if fname == "konstantin_marbach_real.jpg" or not fname.endswith((".jpg", ".jpeg", ".png")):
            continue
        path = os.path.join(TEST_DATA_DIR, fname)
        print(f"\n📸 Comparing reference with: {fname}")
        result = engine.compare(ref_path, path)
        print(f"  Similarity: {result.get('similarity', 0):.4f}")
        print(f"  Is match:   {result.get('is_match', False)}")
        print(f"  Distance:   {result.get('distance', 'N/A')}")


def test_batch_comparison():
    """Test batch face comparison."""
    print("\n" + "=" * 60)
    print("TEST: Batch Face Comparison")
    print("=" * 60)

    engine = FaceEngine(backend="deepface", model="ArcFace")

    ref_path = os.path.join(TEST_DATA_DIR, "konstantin_marbach_real.jpg")
    if not os.path.exists(ref_path):
        print("⚠️ Reference photo not found")
        return

    candidates = []
    for fname in sorted(os.listdir(TEST_DATA_DIR)):
        if fname.endswith((".jpg", ".jpeg", ".png")):
            candidates.append(os.path.join(TEST_DATA_DIR, fname))

    print(f"\n📸 Batch comparing {len(candidates)} images against reference...")
    results = engine.batch_compare(ref_path, candidates)

    for r in results:
        name = os.path.basename(r.get("path", "?"))
        sim = r.get("similarity", 0)
        match_str = "✅" if r.get("is_match") else "❌"
        grade = r.get("quality_grade", "?")
        print(f"  {match_str} {name}: similarity={sim:.4f}, quality={grade}")


def test_dlib_backend():
    """Test dlib backend for comparison."""
    print("\n" + "=" * 60)
    print("TEST: dlib Backend Comparison")
    print("=" * 60)

    engine = FaceEngine(backend="dlib")

    ref_path = os.path.join(TEST_DATA_DIR, "konstantin_marbach_real.jpg")
    if not os.path.exists(ref_path):
        print("⚠️ Reference photo not found")
        return

    # Self-comparison
    result = engine.compare(ref_path, ref_path)
    print(f"\n📸 dlib self-comparison:")
    print(f"  Similarity: {result.get('similarity', 0):.4f}")
    print(f"  Is match:   {result.get('is_match', False)}")
    print(f"  Backend:    {result.get('backend', 'N/A')}")


if __name__ == "__main__":
    test_quality_assessment()
    test_face_analysis()
    test_face_comparison()
    test_batch_comparison()
    test_dlib_backend()
    print("\n✅ All tests completed!")
