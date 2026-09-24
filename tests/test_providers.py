import asyncio

import httpx
import pytest

from app.domain.identity import IdentityHints
from app.domain.image import ImageMatchType
from app.domain.investigation import ProviderOutcome, WebEntity
from app.providers.base import MalformedResponse, ProviderError, ProviderRateLimited, check_response, run_provider
from app.providers.reverse_image.google_vision import GoogleCloudVisionWebDetectionProvider, parse_web_detection
from app.providers.reverse_image.tineye import TinEyeProvider, parse_tineye_response
from app.services.candidate_discovery_service import QueryPlanner
from tests.conftest import make_settings

VISION_RESPONSE = {
    "responses": [
        {
            "webDetection": {
                "webEntities": [{"entityId": "/m/1", "score": 0.9, "description": "Jane Doe"}],
                "fullMatchingImages": [{"url": "https://cdn.example.com/full.jpg"}],
                "partialMatchingImages": [{"url": "https://cdn.example.com/partial.jpg"}],
                "pagesWithMatchingImages": [
                    {
                        "url": "https://example.org/team",
                        "pageTitle": "<b>Jane</b> Doe - Team",
                        "fullMatchingImages": [{"url": "https://example.org/jane.jpg"}],
                    },
                    {
                        "url": "https://news.example.com/a",
                        "partialMatchingImages": [{"url": "https://news.example.com/c.jpg"}],
                    },
                    {"url": "https://unknown.example.com/x"},
                ],
                "visuallySimilarImages": [{"url": "https://sim.example.com/1.jpg"}],
                "bestGuessLabels": [{"label": "jane doe", "languageCode": "en"}],
            }
        }
    ]
}


def test_google_vision_parsing():
    out = parse_web_detection(VISION_RESPONSE, "ref1")
    kinds = {(r.page_url, r.image_url): r.match_type for r in out.results}
    assert kinds[("https://example.org/team", "https://example.org/jane.jpg")] == ImageMatchType.EXACT
    assert kinds[("https://news.example.com/a", "https://news.example.com/c.jpg")] == ImageMatchType.PARTIAL
    assert kinds[("https://unknown.example.com/x", None)] == ImageMatchType.PARTIAL
    assert kinds[(None, "https://cdn.example.com/full.jpg")] == ImageMatchType.EXACT
    assert kinds[(None, "https://sim.example.com/1.jpg")] == ImageMatchType.VISUALLY_SIMILAR
    team = next(r for r in out.results if r.page_url == "https://example.org/team")
    assert team.page_title == "Jane Doe - Team" and team.reference_image_id == "ref1"
    assert out.web_entities[0] == WebEntity(description="Jane Doe", score=0.9, entity_id="/m/1")
    assert out.best_guess_labels == ["jane doe"]


def test_google_vision_errors():
    with pytest.raises(ProviderRateLimited):
        parse_web_detection({"responses": [{"error": {"code": 8, "message": "Quota exceeded"}}]}, None)
    with pytest.raises(ProviderError):
        parse_web_detection({"responses": [{"error": {"code": 3, "message": "Bad image"}}]}, None)
    with pytest.raises(MalformedResponse):
        parse_web_detection({"unexpected": True}, None)
    assert parse_web_detection({"responses": [{}]}, None).results == []


async def test_google_vision_not_configured_and_http(tmp_path):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    assert not GoogleCloudVisionWebDetectionProvider(make_settings(tmp_path), client).is_configured()
    s = make_settings(tmp_path, google_vision_enabled=True, google_vision_api_key="k")
    provider = GoogleCloudVisionWebDetectionProvider(s, client)
    assert provider.is_configured()

    from app.domain.image import ImageQuality, QualityLabel, ReferenceImage
    from app.providers.reverse_image.base import ReverseImageQuery

    ref = ReferenceImage(
        id="r",
        investigation_id="i",
        mime="image/jpeg",
        size_bytes=1,
        width=1,
        height=1,
        sha256="x",
        phash="0" * 16,
        quality=ImageQuality(label=QualityLabel.GOOD, width=1, height=1, face_count=0),
    )
    _, run = await run_provider(
        "google_vision", "reverse_image", lambda: provider.search(ReverseImageQuery(ref, b"x")), timeout=5
    )
    assert run.outcome == ProviderOutcome.RATE_LIMITED


def test_tineye_parsing(tmp_path):
    payload = {
        "status": "ok",
        "results": {
            "matches": [
                {
                    "image_url": "https://img.example.com/1.jpg",
                    "score": 99,
                    "query_match_percent": 99,
                    "target_overlap_percent": 98,
                    "backlinks": [
                        {"url": "https://img.example.com/1.jpg", "backlink": "https://site.example.com/post"}
                    ],
                },
                {
                    "image_url": "https://img.example.com/2.jpg",
                    "query_match_percent": 40,
                    "target_overlap_percent": 95,
                    "backlinks": [],
                },
                {"image_url": "https://img.example.com/3.jpg", "query_match_percent": 85, "target_overlap_percent": 85},
            ]
        },
    }
    out = parse_tineye_response(payload, "r")
    assert [r.match_type for r in out.results] == [
        ImageMatchType.EXACT,
        ImageMatchType.PARTIAL,
        ImageMatchType.MODIFIED,
    ]
    assert out.results[0].page_url == "https://site.example.com/post"
    assert all(r.provider == "tineye" for r in out.results)
    with pytest.raises(ProviderError):
        parse_tineye_response({"status": "error", "messages": ["invalid key"]}, None)
    with pytest.raises(MalformedResponse):
        parse_tineye_response({"status": "ok", "results": {}}, None)
    assert not TinEyeProvider(make_settings(tmp_path), None).is_configured()  # type: ignore[arg-type]
    assert TinEyeProvider(make_settings(tmp_path, tineye_enabled=True, tineye_api_key="k"), None).is_configured()  # type: ignore[arg-type]


def test_check_response_mapping():
    with pytest.raises(ProviderRateLimited):
        check_response(httpx.Response(429), "p")
    with pytest.raises(ProviderRateLimited):
        check_response(httpx.Response(403, text="Quota exceeded for project"), "p")
    with pytest.raises(ProviderError):
        check_response(httpx.Response(500), "p")


async def test_run_provider_outcomes():
    async def slow():
        await asyncio.sleep(1)
        return [1]

    async def boom():
        raise KeyError("api changed")

    async def empty():
        return []

    _, run = await run_provider("p", "s", slow, timeout=0.01)
    assert run.outcome == ProviderOutcome.TIMEOUT
    _, run = await run_provider("p", "s", boom, timeout=1)
    assert run.outcome == ProviderOutcome.FAILED and "KeyError" in run.error
    _, run = await run_provider("p", "s", empty, timeout=1)
    assert run.outcome == ProviderOutcome.NO_RESULTS


def test_query_planner_is_bounded_and_deduplicated():
    planner = QueryPlanner(max_queries=12)
    plan = planner.plan(
        IdentityHints(
            name="Jane Doe",
            location="Vienna",
            employer="Example GmbH",
            university="TU Wien",
            usernames=["janedoe93", "JaneDoe93"],
            emails=["jane@example.com"],
        )
    )
    queries = [p.query for p in plan]
    assert len(queries) <= 12 and len(queries) == len({q.lower() for q in queries})
    assert '"Jane Doe" Vienna' in queries and '"janedoe93"' in queries
    assert any("site:xing.com" in q for q in queries)  # Vienna → Austria → DACH
    assert planner.plan(IdentityHints()) == []
    hinted = planner.plan(
        IdentityHints(), [WebEntity(description="Jane Doe", score=0.8), WebEntity(description="Logo")]
    )
    assert [p.purpose for p in hinted] == ["web_entity"]
