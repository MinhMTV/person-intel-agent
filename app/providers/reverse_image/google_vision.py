"""Google Cloud Vision — Web Detection provider (REST).

Authentication (never committed, read from the environment):
* ``GOOGLE_VISION_API_KEY``  — API key, or
* ``GOOGLE_APPLICATION_CREDENTIALS`` — service-account JSON (needs ``google-auth``)
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

import httpx

from app.config import Settings
from app.domain.image import ImageDiscoveryResult, ImageMatchType
from app.domain.investigation import WebEntity
from app.providers.base import (
    MalformedResponse,
    ProviderError,
    ProviderNotConfigured,
    ProviderRateLimited,
    check_response,
    json_or_raise,
)
from app.providers.reverse_image.base import ReverseImageProvider, ReverseImageQuery, ReverseImageResponse

ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
SCOPE = "https://www.googleapis.com/auth/cloud-vision"


def parse_web_detection(payload: Any, reference_image_id: str | None, max_results: int = 30) -> ReverseImageResponse:
    """Normalise a Vision ``images:annotate`` response into domain objects."""
    if not isinstance(payload, dict) or not isinstance(payload.get("responses"), list) or not payload["responses"]:
        raise MalformedResponse("google_vision: unexpected response shape")
    first = payload["responses"][0]
    if not isinstance(first, dict):
        raise MalformedResponse("google_vision: unexpected response shape")
    if "error" in first:
        err = first["error"] or {}
        code = err.get("code")
        message = str(err.get("message", ""))[:200]
        if code in (8, 429) or "quota" in message.lower():
            raise ProviderRateLimited(f"google_vision: {message}")
        raise ProviderError(f"google_vision: API error {code}: {message}")
    web = first.get("webDetection") or {}
    out = ReverseImageResponse()
    seen: set[tuple[str | None, str | None, str]] = set()

    def add(match_type: ImageMatchType, image_url: str | None, page_url: str | None, title: str | None, score: Any) -> None:
        if not image_url and not page_url:
            return
        key = (image_url, page_url, match_type.value)
        if key in seen:
            return
        seen.add(key)
        out.results.append(
            ImageDiscoveryResult(
                provider="google_vision",
                match_type=match_type,
                image_url=image_url,
                page_url=page_url,
                page_title=_clean_title(title),
                provider_score=float(score) if isinstance(score, (int, float)) else None,
                reference_image_id=reference_image_id,
            )
        )

    for page in (web.get("pagesWithMatchingImages") or [])[:max_results]:
        if not isinstance(page, dict) or not page.get("url"):
            continue
        full = page.get("fullMatchingImages") or []
        partial = page.get("partialMatchingImages") or []
        for img in full:
            add(ImageMatchType.EXACT, img.get("url"), page["url"], page.get("pageTitle"), page.get("score"))
        for img in partial:
            add(ImageMatchType.PARTIAL, img.get("url"), page["url"], page.get("pageTitle"), page.get("score"))
        if not full and not partial:
            # Google says the page contains a matching image but not which one.
            add(ImageMatchType.PARTIAL, None, page["url"], page.get("pageTitle"), page.get("score"))
    for img in (web.get("fullMatchingImages") or [])[:max_results]:
        if isinstance(img, dict):
            add(ImageMatchType.EXACT, img.get("url"), None, None, img.get("score"))
    for img in (web.get("partialMatchingImages") or [])[:max_results]:
        if isinstance(img, dict):
            add(ImageMatchType.PARTIAL, img.get("url"), None, None, img.get("score"))
    for img in (web.get("visuallySimilarImages") or [])[:max_results]:
        if isinstance(img, dict):
            add(ImageMatchType.VISUALLY_SIMILAR, img.get("url"), None, None, img.get("score"))
    for ent in web.get("webEntities") or []:
        if isinstance(ent, dict) and ent.get("description"):
            out.web_entities.append(
                WebEntity(description=str(ent["description"])[:120], score=ent.get("score"), entity_id=ent.get("entityId"))
            )
    out.best_guess_labels = [
        str(lbl["label"])[:120] for lbl in web.get("bestGuessLabels") or [] if isinstance(lbl, dict) and lbl.get("label")
    ]
    return out


def _clean_title(title: str | None) -> str | None:
    if not title:
        return None
    import re

    return re.sub(r"<[^>]+>", "", title)[:200]  # pageTitle contains <b> highlight tags


class GoogleCloudVisionWebDetectionProvider(ReverseImageProvider):
    name = "google_vision"
    version = "1"

    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client
        self._token: str | None = None
        self._token_expiry = 0.0
        self._credentials: Any = None

    def is_configured(self) -> bool:
        if not self.settings.google_vision_enabled:
            return False
        if self.settings.google_vision_api_key:
            return True
        path = self.settings.google_application_credentials
        return bool(path and path.is_file())

    def cache_identity(self) -> dict[str, object]:
        return {**super().cache_identity(), "max_results": self.settings.google_vision_max_results}

    async def _auth(self) -> tuple[dict[str, str], dict[str, str]]:
        if self.settings.google_vision_api_key:
            return {}, {"key": self.settings.google_vision_api_key.get_secret_value()}
        if self._token and time.time() < self._token_expiry - 60:
            return {"Authorization": f"Bearer {self._token}"}, {}
        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account
        except ImportError as exc:
            raise ProviderNotConfigured("google_vision: install 'google-auth' to use service-account credentials") from exc

        def refresh() -> tuple[str, float]:
            if self._credentials is None:
                self._credentials = service_account.Credentials.from_service_account_file(
                    str(self.settings.google_application_credentials), scopes=[SCOPE]
                )
            self._credentials.refresh(Request())
            expiry = self._credentials.expiry.timestamp() if self._credentials.expiry else time.time() + 1800
            return self._credentials.token, expiry

        self._token, self._token_expiry = await asyncio.to_thread(refresh)
        return {"Authorization": f"Bearer {self._token}"}, {}

    async def search(self, query: ReverseImageQuery) -> ReverseImageResponse:
        if not self.is_configured():
            raise ProviderNotConfigured("google_vision: not configured")
        headers, params = await self._auth()
        body = {
            "requests": [
                {
                    "image": {"content": base64.b64encode(query.image_bytes).decode("ascii")},
                    "features": [{"type": "WEB_DETECTION", "maxResults": self.settings.google_vision_max_results}],
                    "imageContext": {"webDetectionParams": {"includeGeoResults": False}},
                }
            ]
        }
        resp = await self.client.post(ENDPOINT, json=body, headers=headers, params=params)
        check_response(resp, self.name)
        return parse_web_detection(json_or_raise(resp, self.name), query.reference.id, self.settings.google_vision_max_results)
