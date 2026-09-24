"""Reverse-image provider interface.

Reverse-image providers answer *"where does this PHOTO (or a copy / crop of
it) appear?"*. They do not perform facial identity matching — that happens
locally in :mod:`app.services.face_matching_service`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.domain.image import ImageDiscoveryResult, ReferenceImage
from app.domain.investigation import WebEntity


@dataclass
class ReverseImageQuery:
    reference: ReferenceImage
    image_bytes: bytes  # normalised JPEG (EXIF stripped)


class ReverseImageResponse(BaseModel):
    results: list[ImageDiscoveryResult] = Field(default_factory=list)
    web_entities: list[WebEntity] = Field(default_factory=list)
    best_guess_labels: list[str] = Field(default_factory=list)

    def __len__(self) -> int:
        return len(self.results)


class ReverseImageProvider(ABC):
    name: str = "reverse_image"
    version: str = "1"

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    async def search(self, query: ReverseImageQuery) -> ReverseImageResponse: ...

    def cache_identity(self) -> dict[str, object]:
        """Provider options that influence results (part of the cache key)."""
        return {"provider": self.name, "version": self.version}
