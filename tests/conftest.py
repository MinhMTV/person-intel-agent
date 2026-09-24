from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.container import Container, build_container
from app.infrastructure.http.safe_fetcher import SafeFetcher
from tests.fakes import FakeFaceBackend, WebWorld, public_resolver


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        app_env="test",
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'data' / 'test.db'}",
        google_vision_enabled=False,
        tineye_enabled=False,
        searxng_enabled=False,
        ddgs_enabled=False,
        github_enabled=False,
        wikidata_enabled=False,
        use_web_entity_hints=True,
        outbound_use_env_proxy=False,
        rate_limit_per_minute=0,
        investigation_timeout=60,
        http_timeout_seconds=5,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def world() -> WebWorld:
    return WebWorld()


@pytest.fixture
def make_container(tmp_path: Path, world: WebWorld) -> Callable[..., Container]:
    created: list[Container] = []

    def factory(settings: Settings | None = None, **kwargs) -> Container:
        s = settings or make_settings(tmp_path)
        transport = httpx.MockTransport(world.handler)
        kwargs.setdefault("face_backend", FakeFaceBackend())
        kwargs.setdefault("reverse_providers", [])
        kwargs.setdefault("search_providers", [])
        kwargs.setdefault("profile_providers", [])
        kwargs.setdefault("fetcher", SafeFetcher(s, resolver=public_resolver, transport=transport))
        kwargs.setdefault("api_client", httpx.AsyncClient(transport=transport))
        container = build_container(s, **kwargs)
        created.append(container)
        return container

    yield factory
    for c in created:
        try:
            c.cache.close()
            c.repo.close()
        except Exception:
            pass
