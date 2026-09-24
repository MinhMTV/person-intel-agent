"""Shared provider error types and the resilient execution wrapper.

Every provider call goes through :func:`run_provider`, which converts any
failure into an explicit :class:`ProviderOutcome` so one failing provider can
never fail the whole investigation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from app.domain.investigation import ProviderOutcome, ProviderRun
from app.infrastructure.security.redaction import safe_error

logger = logging.getLogger(__name__)
T = TypeVar("T")


class ProviderError(Exception):
    outcome = ProviderOutcome.FAILED


class ProviderNotConfigured(ProviderError):
    outcome = ProviderOutcome.NOT_CONFIGURED


class ProviderRateLimited(ProviderError):
    outcome = ProviderOutcome.RATE_LIMITED


class ProviderTimeout(ProviderError):
    outcome = ProviderOutcome.TIMEOUT


class MalformedResponse(ProviderError):
    outcome = ProviderOutcome.FAILED


def check_response(resp: httpx.Response, provider: str) -> None:
    """Map HTTP status codes to provider errors."""
    if resp.status_code == 429:
        raise ProviderRateLimited(f"{provider}: rate limited (HTTP 429)")
    if resp.status_code in (401, 403):
        body = resp.text[:300].lower()
        if "quota" in body or "rate" in body or "limit" in body:
            raise ProviderRateLimited(f"{provider}: quota exceeded (HTTP {resp.status_code})")
        raise ProviderError(f"{provider}: authorisation failed (HTTP {resp.status_code})")
    if resp.status_code >= 400:
        raise ProviderError(f"{provider}: HTTP {resp.status_code}")


def json_or_raise(resp: httpx.Response, provider: str) -> object:
    try:
        return resp.json()
    except ValueError as exc:
        raise MalformedResponse(f"{provider}: response is not valid JSON") from exc


async def run_provider(
    provider: str,
    stage: str,
    call: Callable[[], Awaitable[T]],
    *,
    timeout: float,
    count: Callable[[T], int] = lambda r: len(r) if hasattr(r, "__len__") else 1,  # type: ignore[arg-type]
    detail: str | None = None,
    cache_hit: bool = False,
) -> tuple[T | None, ProviderRun]:
    started = time.perf_counter()
    run = ProviderRun(provider=provider, stage=stage, outcome=ProviderOutcome.SUCCESS, detail=detail, cache_hit=cache_hit)
    result: T | None = None
    try:
        result = await asyncio.wait_for(call(), timeout=timeout)
        run.result_count = count(result)
        run.outcome = ProviderOutcome.SUCCESS if run.result_count else ProviderOutcome.NO_RESULTS
    except ProviderError as exc:
        run.outcome = exc.outcome
        run.error = safe_error(exc)
    except (TimeoutError, httpx.TimeoutException) as exc:
        run.outcome = ProviderOutcome.TIMEOUT
        run.error = safe_error(exc) if str(exc) else "Timed out"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # malformed payloads, API changes, network errors …
        logger.warning("Provider %s failed: %s", provider, safe_error(exc))
        run.outcome = ProviderOutcome.FAILED
        run.error = safe_error(exc)
    run.duration_ms = int((time.perf_counter() - started) * 1000)
    return result, run
