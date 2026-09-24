"""Retention policy for biometric / uploaded data.

* stored reference images: deleted after REFERENCE_IMAGE_RETENTION_HOURS
* reference face embeddings: deleted after REFERENCE_EMBEDDING_RETENTION_HOURS
* candidate face thumbnails inside stored results: stripped after
  FACE_THUMBNAIL_RETENTION_DAYS
* expired cache entries (incl. cached candidate embeddings) are purged

No permanent face database is built: embeddings only exist for the running
investigation window and in TTL-limited caches.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.infrastructure.cache.store import CacheStore
from app.infrastructure.persistence.repository import InvestigationRepository

logger = logging.getLogger(__name__)


class RetentionService:
    def __init__(self, settings: Settings, repo: InvestigationRepository, cache: CacheStore | None):
        self.settings = settings
        self.repo = repo
        self.cache = cache

    def purge(self) -> dict[str, int]:
        now = datetime.now(UTC)
        s = self.settings
        stats = {"reference_files": 0, "embeddings": 0, "thumbnails": 0, "cache": 0}
        file_cutoff = now - timedelta(hours=s.reference_image_retention_hours)
        emb_cutoff = now - timedelta(hours=s.reference_embedding_retention_hours)
        for ref in self.repo.reference_images_created_before(max(file_cutoff, emb_cutoff)):
            purge_file = ref.created_at < file_cutoff and bool(ref.stored_path)
            purge_emb = ref.created_at < emb_cutoff
            if purge_file:
                path = s.upload_path(ref.stored_path)
                if path:
                    path.unlink(missing_ok=True)
                stats["reference_files"] += 1
            if purge_emb:
                stats["embeddings"] += 1
            self.repo.purge_reference_image_data(ref.id, file=purge_file, embeddings=purge_emb)
        thumb_cutoff = now - timedelta(days=s.face_thumbnail_retention_days)
        for inv_id in self.repo.results_completed_before(max(thumb_cutoff, file_cutoff)):
            result = self.repo.get_result(inv_id)
            if result is None or result.completed_at is None:
                continue
            changed = False
            if result.completed_at < file_cutoff:  # reference-face crops follow the reference-image policy
                for ref in result.reference_images:
                    for face in ref.faces:
                        if face.thumbnail:
                            face.thumbnail = None
                            changed = True
            if result.completed_at < thumb_cutoff:
                for cand in result.candidates:
                    for ev in cand.evidence:
                        if ev.face and ev.face.candidate_thumbnail:
                            ev.face.candidate_thumbnail = None
                            changed = True
                    if cand.best_face and cand.best_face.candidate_thumbnail:
                        cand.best_face.candidate_thumbnail = None
                        changed = True
                for page in result.pages:
                    for img in page.images:
                        for face in img.faces:
                            if face.thumbnail:
                                face.thumbnail = None
                                changed = True
            if changed:
                self.repo.save_result(result)
                stats["thumbnails"] += 1
        if self.cache is not None:
            stats["cache"] = self.cache.purge_expired()
        if any(stats.values()):
            logger.info("Retention purge: %s", stats)
        return stats
