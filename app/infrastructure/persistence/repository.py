"""Repository interface. SQLite implements it today; a PostgreSQL
implementation only needs to provide the same methods."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.image import ReferenceImage
from app.domain.investigation import (
    Investigation,
    InvestigationResult,
    InvestigationStatus,
    Note,
    ProgressEvent,
)


class InvestigationRepository(ABC):
    # --- investigations ---------------------------------------------------------
    @abstractmethod
    def create(self, investigation: Investigation) -> None: ...

    @abstractmethod
    def get(self, investigation_id: str, *, with_result: bool = True) -> Investigation | None: ...

    @abstractmethod
    def list_investigations(
        self, *, limit: int = 50, tag: str | None = None, query: str | None = None
    ) -> list[Investigation]: ...

    @abstractmethod
    def update_meta(self, investigation: Investigation) -> None:
        """Persist status, hints, options, fingerprint, error, pinned."""

    @abstractmethod
    def set_status(self, investigation_id: str, status: InvestigationStatus, error: str | None = None) -> None: ...

    @abstractmethod
    def delete(self, investigation_id: str) -> bool: ...

    @abstractmethod
    def mark_interrupted(self) -> int:
        """Mark investigations that were running when the process died."""

    @abstractmethod
    def candidate_summary(self, investigation_id: str) -> tuple[int, str | None, str | None]:
        """(candidate count, top candidate name, top evidence level)."""

    # --- results -------------------------------------------------------------------
    @abstractmethod
    def save_result(self, result: InvestigationResult) -> None: ...

    @abstractmethod
    def get_result(self, investigation_id: str) -> InvestigationResult | None: ...

    # --- reference images ------------------------------------------------------------
    @abstractmethod
    def save_reference_image(self, image: ReferenceImage) -> None:
        """Insert/update metadata and (if present) face embeddings."""

    @abstractmethod
    def get_reference_images(self, investigation_id: str, *, with_embeddings: bool = False) -> list[ReferenceImage]: ...

    @abstractmethod
    def delete_reference_image(self, investigation_id: str, image_id: str) -> ReferenceImage | None: ...

    # --- events --------------------------------------------------------------------------
    @abstractmethod
    def append_event(self, event: ProgressEvent) -> ProgressEvent: ...

    @abstractmethod
    def list_events(self, investigation_id: str, after_seq: int = 0) -> list[ProgressEvent]: ...

    @abstractmethod
    def clear_events(self, investigation_id: str) -> None: ...

    # --- organisation ---------------------------------------------------------------------
    @abstractmethod
    def add_note(self, investigation_id: str, text: str) -> Note: ...

    @abstractmethod
    def delete_note(self, investigation_id: str, note_id: int) -> bool: ...

    @abstractmethod
    def add_tag(self, investigation_id: str, tag: str) -> list[str]: ...

    @abstractmethod
    def remove_tag(self, investigation_id: str, tag: str) -> list[str]: ...

    @abstractmethod
    def all_tags(self) -> dict[str, int]: ...

    # --- retention ---------------------------------------------------------------------------
    @abstractmethod
    def reference_images_created_before(self, cutoff: datetime) -> list[ReferenceImage]: ...

    @abstractmethod
    def purge_reference_image_data(self, image_id: str, *, file: bool, embeddings: bool) -> None: ...

    @abstractmethod
    def results_completed_before(self, cutoff: datetime) -> list[str]: ...

    @abstractmethod
    def close(self) -> None: ...
