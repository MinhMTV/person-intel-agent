"""Centralised, typed application settings.

All configuration comes from environment variables (optionally loaded from a
``.env`` file). Secrets are held as ``SecretStr`` so they never appear in
``repr()``/log output. See ``.env.example`` for the documented variables.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Bumped whenever a change alters what an investigation would return for the
# same inputs (matching thresholds, parsing rules, fusion rules …). It is part
# of every cache key and of the investigation fingerprint.
PIPELINE_VERSION = "2"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,  # "KEY=" in .env means "use the default"
    )

    # --- Application ------------------------------------------------------
    app_env: Literal["development", "production", "test"] = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    # Optional shared secret. When set, every /api route requires it
    # (header ``X-API-Token`` or the ``pia_token`` cookie set via /api/auth).
    app_api_token: SecretStr | None = None
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    rate_limit_per_minute: int = 30

    data_dir: Path = PROJECT_ROOT / "data"
    database_url: str = ""  # default: sqlite:///<data_dir>/person_intel.db

    # --- Uploads / images ---------------------------------------------------
    max_upload_mb: float = 10.0
    max_reference_images: int = 5
    max_image_pixels: int = 40_000_000
    min_image_dimension: int = 64
    max_image_dimension: int = 12_000

    # --- Remote fetching ------------------------------------------------------
    max_remote_image_mb: float = 8.0
    max_remote_page_mb: float = 3.0
    http_timeout_seconds: float = 12.0
    http_max_redirects: int = 4
    http_user_agent: str = "PersonIntelAgent/0.6 (+candidate verification; respects robots)"
    # Use HTTP(S)_PROXY from the environment for outbound requests. When a
    # proxy is used, SSRF protection falls back to pre-flight DNS validation.
    outbound_use_env_proxy: bool = True

    # --- Investigation limits -------------------------------------------------
    investigation_timeout: int = 180
    max_concurrent_investigations: int = 2
    max_candidate_pages: int = 40
    max_images_per_page: int = 4
    max_similar_images: int = 10
    page_fetch_concurrency: int = 6
    image_fetch_concurrency: int = 6
    face_worker_concurrency: int = 2
    max_search_queries: int = 12

    # --- Face matching --------------------------------------------------------
    face_model: str = "ArcFace"
    face_detector: str = "opencv"
    # ArcFace cosine-distance band boundaries (lower distance = more similar).
    # FACE_MATCH_THRESHOLD is DeepFace's published ArcFace verification
    # threshold. The other bands are heuristic, NOT calibrated probabilities.
    face_very_high_threshold: float = 0.40
    face_high_threshold: float = 0.55
    face_match_threshold: float = 0.68
    face_low_threshold: float = 0.80
    enable_demographics: bool = False  # age/gender/emotion — never used for matching
    phash_duplicate_distance: int = 6  # Hamming distance (64-bit pHash)

    # --- Providers --------------------------------------------------------------
    google_vision_enabled: bool = False
    google_application_credentials: Path | None = None
    google_vision_api_key: SecretStr | None = None
    google_vision_max_results: int = 30

    tineye_enabled: bool = False
    tineye_api_url: str = "https://api.tineye.com/rest/"
    tineye_api_key: SecretStr | None = None

    searxng_enabled: bool = True
    searxng_url: str = "http://127.0.0.1:8888"
    searxng_engines: str = "google,duckduckgo,startpage,bing"
    ddgs_enabled: bool = True
    github_enabled: bool = True
    github_token: SecretStr | None = None
    wikidata_enabled: bool = True
    use_web_entity_hints: bool = True

    # Lead generation (hypotheses only — never treated as discoveries)
    generate_email_candidates: bool = False
    smtp_verification_enabled: bool = False
    generate_domain_candidates: bool = False
    hibp_api_key: SecretStr | None = None

    # --- Browser sessions (LinkedIn/Xing/… cookies) -----------------------------
    session_management_enabled: bool = False
    session_persistence_enabled: bool = False
    session_encryption_key: SecretStr | None = None

    # --- Retention -----------------------------------------------------------------
    reference_image_retention_hours: float = 24.0
    reference_embedding_retention_hours: float = 24.0
    face_thumbnail_retention_days: float = 30.0

    # --- Cache TTLs (seconds) ------------------------------------------------------
    cache_ttl_web_search: int = 6 * 3600
    cache_ttl_reverse_image: int = 24 * 3600
    cache_ttl_page: int = 6 * 3600
    cache_ttl_image_meta: int = 24 * 3600
    cache_ttl_face_embedding: int = 24 * 3600

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'person_intel.db'}"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def session_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)

    @property
    def max_remote_image_bytes(self) -> int:
        return int(self.max_remote_image_mb * 1024 * 1024)

    @property
    def max_remote_page_bytes(self) -> int:
        return int(self.max_remote_page_mb * 1024 * 1024)

    def config_fingerprint(self) -> dict[str, object]:
        """Settings that change investigation results (used in cache keys)."""
        return {
            "pipeline": PIPELINE_VERSION,
            "face_model": self.face_model,
            "face_detector": self.face_detector,
            "bands": [
                self.face_very_high_threshold,
                self.face_high_threshold,
                self.face_match_threshold,
                self.face_low_threshold,
            ],
            "max_pages": self.max_candidate_pages,
            "max_images_per_page": self.max_images_per_page,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


def ensure_data_dirs(settings: Settings) -> None:
    """Create private data directories (0700)."""
    for path in (settings.data_dir, settings.upload_dir, settings.session_dir):
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(0o700)
        except OSError:  # pragma: no cover - e.g. Windows
            pass
