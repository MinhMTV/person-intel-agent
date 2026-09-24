"""Per-run context shared by the pipeline stages (progress, stats, runs)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.domain.identity import IdentityHints, InvestigationOptions
from app.domain.investigation import EventType, InvestigationStats, ProviderRun

logger = logging.getLogger(__name__)

ProgressSink = Callable[[EventType, str, dict[str, Any]], None]


def _null_sink(_type: EventType, _message: str, _data: dict[str, Any]) -> None:
    return None


@dataclass
class RunContext:
    investigation_id: str
    hints: IdentityHints
    options: InvestigationOptions = field(default_factory=InvestigationOptions)
    sink: ProgressSink = _null_sink
    stats: InvestigationStats = field(default_factory=InvestigationStats)
    runs: list[ProviderRun] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def emit(self, type_: EventType, message: str = "", **data: Any) -> None:
        try:
            self.sink(type_, message, data)
        except Exception:  # progress reporting must never break a run
            logger.exception("progress sink failed")

    def record_run(self, run: ProviderRun) -> None:
        self.runs.append(run)
        if run.cache_hit:
            self.stats.cache_hits += 1
        self.emit(
            EventType.PROVIDER_FINISHED,
            f"{run.provider}: {run.outcome.value.lower().replace('_', ' ')}",
            provider=run.provider,
            stage=run.stage,
            outcome=run.outcome.value,
            result_count=run.result_count,
            duration_ms=run.duration_ms,
            cache_hit=run.cache_hit,
            error=run.error,
        )

    def cache(self, hit: bool) -> None:
        if hit:
            self.stats.cache_hits += 1
        else:
            self.stats.cache_misses += 1

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
            self.emit(EventType.WARNING, message)
