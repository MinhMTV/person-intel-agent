import io

import pytest
from PIL import Image

from app.domain.image import QualityLabel
from app.vision.image_hash import hamming_distance, phash
from app.vision.image_validation import ImageValidationError, validate_image_bytes
from tests.fakes import JANE, OTHER, make_image

MB = 1024 * 1024


def _encode(img: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return buf.getvalue()


@pytest.mark.parametrize("fmt,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")])
def test_accepts_supported_formats(fmt, mime):
    data = _encode(Image.new("RGB", (200, 150), "red"), fmt)
    v = validate_image_bytes(data, max_bytes=MB)
    assert v.mime == mime and (v.width, v.height) == (200, 150)
    assert len(v.original_sha256) == 64


def test_rejects_gif_and_non_images():
    with pytest.raises(ImageValidationError) as e:
        validate_image_bytes(_encode(Image.new("RGB", (100, 100)), "GIF"), max_bytes=MB)
    assert e.value.code == "unsupported_format"
    with pytest.raises(ImageValidationError) as e:
        validate_image_bytes(b"<html><script>alert(1)</script></html>", max_bytes=MB)
    assert e.value.code == "invalid"


def test_rejects_truncated_and_corrupted():
    data = _encode(Image.new("RGB", (400, 400), "blue"), "PNG")
    with pytest.raises(ImageValidationError):
        validate_image_bytes(data[: len(data) // 2], max_bytes=MB)


def test_rejects_size_limits_and_bombs():
    data = _encode(Image.new("RGB", (300, 300)), "PNG")
    with pytest.raises(ImageValidationError) as e:
        validate_image_bytes(data, max_bytes=100)
    assert e.value.code == "too_large"
    with pytest.raises(ImageValidationError) as e:
        validate_image_bytes(data, max_bytes=MB, max_pixels=10_000)
    assert e.value.code == "decompression_bomb"
    with pytest.raises(ImageValidationError) as e:
        validate_image_bytes(_encode(Image.new("RGB", (20, 20)), "PNG"), max_bytes=MB)
    assert e.value.code == "too_small"


def test_exif_orientation_is_applied_and_metadata_stripped():
    img = Image.new("RGB", (300, 100), "green")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° CW
    data = _encode(img, "JPEG", exif=exif.tobytes())
    v = validate_image_bytes(data, max_bytes=MB)
    assert (v.width, v.height) == (100, 300)
    reencoded = Image.open(io.BytesIO(v.to_jpeg()))
    assert not reencoded.getexif()


def test_phash_robust_to_resize_and_different_for_other_images():
    a = Image.open(io.BytesIO(make_image([(JANE, (100, 80, 110))], seed=1))).convert("RGB")
    b = a.resize((160, 160))
    c = Image.open(io.BytesIO(make_image([(OTHER, (10, 10, 200))], seed=99))).convert("RGB")
    assert hamming_distance(phash(a), phash(b)) <= 6
    assert hamming_distance(phash(a), phash(c)) > 10
    assert hamming_distance(None, phash(a)) == 64


async def test_reference_pipeline_quality_and_faces(make_container):
    c = make_container()
    inv = c.investigations.create()
    ref = await c.investigations.add_reference_image(inv.id, "../../etc/passwd.png", make_image([(JANE, (100, 80, 110))], seed=1))
    assert ref.filename == "passwd.png"  # display name sanitised, never used as a path
    assert ref.selected_face_id == "f1" and ref.faces[0].thumbnail.startswith("data:image/jpeg")
    assert ref.quality.label in (QualityLabel.GOOD, QualityLabel.USABLE, QualityLabel.POOR)
    assert ref.quality.face_count == 1
    no_face = await c.investigations.add_reference_image(inv.id, "empty.png", make_image(seed=3))
    assert no_face.quality.label == QualityLabel.POOR and not no_face.faces
    assert "embedding" not in ref.model_dump_json()  # biometric data never serialised
