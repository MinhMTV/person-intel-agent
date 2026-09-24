"""Synthetic test world: images, a deterministic face backend, mocked HTTP and providers.

No real person's imagery is used. A "face" is a solid square of an identity
colour; the fake backend detects those squares and returns a fixed embedding
per identity, so face similarity between identities is fully controlled.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import httpx
import numpy as np
from PIL import Image

from app.domain.identity import IdentityHints
from app.domain.image import ImageDiscoveryResult
from app.providers.base import ProviderError
from app.providers.profiles.base import ProfileProvider, ProfileRecord
from app.providers.reverse_image.base import ReverseImageProvider, ReverseImageQuery, ReverseImageResponse
from app.providers.web_search.base import WebSearchHit, WebSearchProvider
from app.vision.face_detector import FaceObservation

DIM = 16
_rng = np.random.default_rng(1234)
_E1 = np.zeros(DIM)
_E1[0] = 1.0
_E2 = np.zeros(DIM)
_E2[1] = 1.0


def _mix(cos: float) -> np.ndarray:
    """Unit vector with the given cosine similarity to the target identity."""
    return cos * _E1 + np.sqrt(max(0.0, 1 - cos * cos)) * _E2


# identity colour → embedding
JANE = (250, 20, 20)  # the target person, photo 1
JANE_OTHER = (20, 250, 20)  # the target person, a different photo
LOOKALIKE = (20, 20, 250)  # different person, looks alike (HIGH band)
LOOKALIKE_VH = (250, 250, 20)  # different person, extremely similar (VERY_HIGH band)
OTHER = (250, 20, 250)  # clearly different person

EMBEDDINGS = {
    JANE: _E1,
    JANE_OTHER: _mix(0.90),  # distance 0.10 → VERY_HIGH
    LOOKALIKE: _mix(0.50),  # distance 0.50 → HIGH
    LOOKALIKE_VH: _mix(0.65),  # distance 0.35 → VERY_HIGH
    OTHER: _mix(0.05),  # distance 0.95 → NO_MATCH
}


def make_image(faces: list[tuple[tuple[int, int, int], tuple[int, int, int]]] | None = None, *, seed: int = 0,
               size: tuple[int, int] = (320, 320), fmt: str = "PNG") -> bytes:
    """Noise background + solid squares. faces = [(colour, (x, y, side)), ...]."""
    rng = np.random.default_rng(seed)
    arr = rng.integers(60, 190, size=(size[1], size[0], 3), dtype=np.uint8)
    for colour, (x, y, side) in faces or []:
        arr[y:y + side, x:x + side] = colour
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format=fmt, **({"quality": 95} if fmt == "JPEG" else {}))
    return buf.getvalue()


def crop_image(data: bytes, box: tuple[int, int, int, int]) -> bytes:
    img = Image.open(io.BytesIO(data)).crop(box)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeFaceBackend:
    name = "fake"
    model: str | None = "FakeFace"

    @property
    def supports_embeddings(self) -> bool:
        return True

    def analyze(self, rgb: np.ndarray) -> list[FaceObservation]:
        faces = []
        for colour, embedding in EMBEDDINGS.items():
            mask = np.all(rgb == np.array(colour, dtype=rgb.dtype), axis=-1)
            if mask.sum() < 64:
                continue
            ys, xs = np.nonzero(mask)
            faces.append(FaceObservation(
                x=int(xs.min()), y=int(ys.min()), w=int(xs.max() - xs.min() + 1), h=int(ys.max() - ys.min() + 1),
                confidence=0.99, embedding=embedding.astype(np.float32),
            ))
        return faces


# --------------------------------------------------------------------------- HTTP
@dataclass
class WebWorld:
    """Maps URLs to (content-type, body) or (status, location) redirects."""

    resources: dict[str, tuple[str, bytes]] = field(default_factory=dict)
    redirects: dict[str, str] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)

    def page(self, url: str, html: str) -> None:
        self.resources[url] = ("text/html; charset=utf-8", html.encode())

    def image(self, url: str, data: bytes, content_type: str = "image/png") -> None:
        self.resources[url] = (content_type, data)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url in self.redirects:
            return httpx.Response(302, headers={"location": self.redirects[url]})
        if url in self.resources:
            content_type, body = self.resources[url]
            return httpx.Response(200, headers={"content-type": content_type}, content=body)
        return httpx.Response(404, headers={"content-type": "text/html"}, content=b"not found")


async def public_resolver(host: str, port: int) -> list[str]:
    if host.startswith("internal"):
        return ["10.0.0.5"]
    return ["93.184.216.34"]


# ---------------------------------------------------------------------- providers
class FakeReverseProvider(ReverseImageProvider):
    name = "fake_reverse"

    def __init__(self, results: list[ImageDiscoveryResult] | None = None, error: Exception | None = None,
                 configured: bool = True):
        self.results = results or []
        self.error = error
        self.configured = configured
        self.calls = 0

    def is_configured(self) -> bool:
        return self.configured

    async def search(self, query: ReverseImageQuery) -> ReverseImageResponse:
        self.calls += 1
        if self.error:
            raise self.error
        return ReverseImageResponse(results=[r.model_copy() for r in self.results])


class FakeSearchProvider(WebSearchProvider):
    name = "fake_search"

    def __init__(self, hits_by_substring: dict[str, list[tuple[str, str, str]]] | None = None,
                 error: Exception | None = None):
        self.hits = hits_by_substring or {}
        self.error = error
        self.queries: list[str] = []

    def is_configured(self) -> bool:
        return True

    async def search(self, query: str, limit: int = 10) -> list[WebSearchHit]:
        self.queries.append(query)
        if self.error:
            raise self.error
        out = []
        for needle, hits in self.hits.items():
            if needle.lower() in query.lower():
                out += [WebSearchHit(url=u, title=t, snippet=s, provider=self.name, query=query) for u, t, s in hits]
        return out


class FakeProfileProvider(ProfileProvider):
    name = "fake_profiles"

    def __init__(self, records: list[ProfileRecord] | None = None, error: Exception | None = None):
        self.records = records or []
        self.error = error

    def is_configured(self) -> bool:
        return True

    def applicable(self, hints: IdentityHints) -> bool:
        return bool(hints.name or hints.usernames)

    async def lookup(self, hints: IdentityHints) -> list[ProfileRecord]:
        if self.error:
            raise self.error
        return list(self.records)


class ExplodingProvider(FakeReverseProvider):
    def __init__(self) -> None:
        super().__init__(error=ProviderError("boom"))
