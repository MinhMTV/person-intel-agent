"""Background execution of investigations + progress event fan-out.

Events are persisted (so a reconnecting client can replay them) and pushed to
live subscribers (SSE). The runner calls exactly the same
``InvestigationService.investigate`` as the CLI.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from app.config import Settings
from app.domain.investigation import TERMINAL_EVENTS, EventType, InvestigationStatus, ProgressEvent
from app.infrastructure.persistence.repository import InvestigationRepository
from app.services.investigation_service import InvestigationError, InvestigationService

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self, repo: InvestigationRepository):
        self.repo = repo
        self._subscribers: dict[str, set[asyncio.Queue[ProgressEvent]]] = defaultdict(set)

    def publish(self, investigation_id: str, type_: EventType, message: str, data: dict[str, Any]) -> ProgressEvent:
        event = self.repo.append_event(
            ProgressEvent(investigation_id=investigation_id, type=type_, message=message, data=data)
        )
        for queue in list(self._subscribers.get(investigation_id, ())):
            with contextlib.suppress(asyncio.QueueFull):  # slow consumer replays from the store
                queue.put_nowait(event)
        return event

    def sink_for(self, investigation_id: str):  # type: ignore[no-untyped-def]
        return lambda type_, message, data: self.publish(investigation_id, type_, message, data)

    async def subscribe(
        self, investigation_id: str, after_seq: int = 0, *, is_active: bool = True, heartbeat: float = 15.0
    ) -> AsyncIterator[ProgressEvent | None]:
        """Replay stored events, then stream live ones. Yields ``None`` as heartbeat."""
        queue: asyncio.Queue[ProgressEvent] = asyncio.Queue(maxsize=1000)
        self._subscribers[investigation_id].add(queue)
        try:
            last = after_seq
            for event in self.repo.list_events(investigation_id, after_seq):
                last = event.seq
                yield event
                if event.type in TERMINAL_EVENTS:
                    return
            if not is_active:
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except TimeoutError:
                    yield None
                    continue
                if event.seq <= last:
                    continue
                last = event.seq
                yield event
                if event.type in TERMINAL_EVENTS:
                    return
        finally:
            self._subscribers[investigation_id].discard(queue)
            if not self._subscribers[investigation_id]:
                self._subscribers.pop(investigation_id, None)


class InvestigationRunner:
    def __init__(self, settings: Settings, service: InvestigationService, repo: InvestigationRepository, bus: EventBus):
        self.settings = settings
        self.service = service
        self.repo = repo
        self.bus = bus
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._semaphore: asyncio.Semaphore | None = None

    def is_running(self, investigation_id: str) -> bool:
        task = self._tasks.get(investigation_id)
        return task is not None and not task.done()

    def start(self, investigation_id: str) -> None:
        if self.is_running(investigation_id):
            raise InvestigationError("Investigation is already running")
        inv = self.repo.get(investigation_id, with_result=False)
        if inv is None:
            raise InvestigationError("Investigation not found")
        self.service.validate_runnable(inv)
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(max(1, self.settings.max_concurrent_investigations))
        self.repo.clear_events(investigation_id)
        self.repo.set_status(investigation_id, InvestigationStatus.QUEUED)
        self._tasks[investigation_id] = asyncio.create_task(self._run(investigation_id))

    async def _run(self, investigation_id: str) -> None:
        assert self._semaphore is not None
        async with self._semaphore:
            try:
                await self.service.investigate(investigation_id, progress=self.bus.sink_for(investigation_id))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Investigation %s ended with an error", investigation_id)
            finally:
                self._tasks.pop(investigation_id, None)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
