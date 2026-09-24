"""Perceptual hashing (64-bit DCT pHash) implemented with numpy only."""

from __future__ import annotations

import numpy as np
from PIL import Image

_N = 32
_K = 8


def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    mat = np.cos(np.pi * (2 * i + 1) * k / (2 * n))
    mat[0, :] *= 1 / np.sqrt(2)
    return mat * np.sqrt(2 / n)


_DCT = _dct_matrix(_N)


def phash(image: Image.Image) -> str:
    """Return the 64-bit perceptual hash of an image as 16 hex chars."""
    gray = image.convert("L").resize((_N, _N), Image.Resampling.LANCZOS)
    pixels = np.asarray(gray, dtype=np.float64)
    coeffs = _DCT @ pixels @ _DCT.T
    low = coeffs[:_K, :_K].flatten()
    median = np.median(low[1:])  # exclude DC term
    bits = low > median
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def hamming_distance(hash_a: str | None, hash_b: str | None) -> int:
    """Bit distance between two hex hashes (64 when either is missing)."""
    if not hash_a or not hash_b or len(hash_a) != len(hash_b):
        return 64
    return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")
