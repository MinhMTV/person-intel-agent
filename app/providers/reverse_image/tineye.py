"""TinEye API provider — exact / near-duplicate image discovery.

TinEye finds copies of the same *image* (resized, re-encoded, cropped or
edited). It is NOT facial recognition: a different photo of the same person
will not be found. Results map to EXACT / MODIFIED / PARTIAL image matches.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from app.domain.image import ImageDiscoveryResult, ImageMatchType
from app.providers.base import MalformedResponse, ProviderError, ProviderNotConfigured, check_response, json_or_raise
from app.providers.reverse_image.base import ReverseImageProvider, ReverseImageQuery, ReverseImageResponse


def _classify(match: dict[str, Any]) -> ImageMatchType:
    """Classify a TinEye match. Uses overlap/percent fields when present."""
    query_pct = match.get("query_match_percent")
    overlap = match.get("target_overlap_percent")
    tags = {str(t).lower() for t in match.get("tags") or []}
    if isinstance(query_pct, (int, float)) and isinstance(overlap, (int, float)):
        if query_pct >= 90 and overlap >= 90:
            return ImageMatchType.EXACT
        if query_pct < 70 or overlap < 70:
            return ImageMatchType.PARTIAL
        return ImageMatchType.MODIFIED
    if "cropped" in tags or "crop" in tags:
        return ImageMatchType.PARTIAL
    score = match.get("score")
    if isinstance(score, (int, float)) and score >= 90:
        return ImageMatchType.EXACT
    return ImageMatchType.MODIFIED


def parse_tineye_response(payload: Any, reference_image_id: str | None, limit: int = 30) -> ReverseImageResponse:
    if not isinstance(payload, dict):
        raise MalformedResponse("tineye: unexpected response shape")
    status = str(payload.get("status", "ok")).lower()
    if status not in ("ok", "success"):
        messages = "; ".join(str(m) for m in payload.get("messages") or [])[:200]
        raise ProviderError(f"tineye: {status} {messages}".strip())
    results = payload.get("results") or {}
    matches = results.get("matches") if isinstance(results, dict) else None
    if matches is None:
        matches = payload.get("matches")
    if not isinstance(matches, list):
        raise MalformedResponse("tineye: missing matches")
    out = ReverseImageResponse()
    for match in matches[:limit]:
        if not isinstance(match, dict):
            continue
        match_type = _classify(match)
        score = match.get("score")
        backlinks = match.get("backlinks") or []
        if not backlinks:
            out.results.append(
                ImageDiscoveryResult(
                    provider="tineye",
                    match_type=match_type,
                    image_url=match.get("image_url"),
                    page_url=None,
                    provider_score=score if isinstance(score, (int, float)) else None,
                    reference_image_id=reference_image_id,
                )
            )
        for backlink in backlinks[:5]:
            if not isinstance(backlink, dict):
                continue
            out.results.append(
                ImageDiscoveryResult(
                    provider="tineye",
                    match_type=match_type,
                    image_url=backlink.get("url") or match.get("image_url"),
                    page_url=backlink.get("backlink"),
                    provider_score=score if isinstance(score, (int, float)) else None,
                    reference_image_id=reference_image_id,
                )
            )
    return out


class TinEyeProvider(ReverseImageProvider):
    name = "tineye"
    version = "1"

    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    def is_configured(self) -> bool:
        return bool(self.settings.tineye_enabled and self.settings.tineye_api_key)

    async def search(self, query: ReverseImageQuery) -> ReverseImageResponse:
        if not self.is_configured() or self.settings.tineye_api_key is None:
            raise ProviderNotConfigured("tineye: not configured")
        url = self.settings.tineye_api_url.rstrip("/") + "/search/"
        resp = await self.client.post(
            url,
            headers={"x-api-key": self.settings.tineye_api_key.get_secret_value()},
            files={"image_upload": ("reference.jpg", query.image_bytes, "image/jpeg")},
            data={"limit": "30", "backlink_limit": "5", "sort": "score", "order": "desc"},
        )
        check_response(resp, self.name)
        return parse_tineye_response(json_or_raise(resp, self.name), query.reference.id)
