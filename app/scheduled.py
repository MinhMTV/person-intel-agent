"""Scheduled search module — save and auto-run searches periodically."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Awaitable

_SCHEDULE_FILE = Path("/tmp/pia_schedules.json")
_schedules: dict[str, dict] = {}
_running_tasks: dict[str, asyncio.Task] = {}


def _load_schedules():
    global _schedules
    if _SCHEDULE_FILE.exists():
        try:
            _schedules = json.loads(_SCHEDULE_FILE.read_text())
        except Exception:
            _schedules = {}


def _save_schedules():
    try:
        _SCHEDULE_FILE.write_text(json.dumps(_schedules, default=str, indent=2))
    except Exception:
        pass


def get_schedules() -> list[dict]:
    _load_schedules()
    return list(_schedules.values())


def add_schedule(name: str, interval_hours: int = 24, enabled: bool = True) -> str:
    """Add a new scheduled search. Returns schedule ID."""
    import uuid
    sid = str(uuid.uuid4())[:8]
    _schedules[sid] = {
        "id": sid,
        "name": name,
        "interval_hours": interval_hours,
        "enabled": enabled,
        "created_at": datetime.utcnow().isoformat(),
        "last_run": None,
        "run_count": 0,
        "last_result": None,
    }
    _save_schedules()
    return sid


def remove_schedule(schedule_id: str) -> bool:
    """Remove a scheduled search."""
    if schedule_id in _schedules:
        del _schedules[schedule_id]
        _save_schedules()
        # Cancel running task if any
        task = _running_tasks.pop(schedule_id, None)
        if task and not task.done():
            task.cancel()
        return True
    return False


def toggle_schedule(schedule_id: str) -> bool | None:
    """Toggle a scheduled search on/off."""
    if schedule_id in _schedules:
        _schedules[schedule_id]["enabled"] = not _schedules[schedule_id]["enabled"]
        _save_schedules()
        return _schedules[schedule_id]["enabled"]
    return None


async def run_scheduled_search(schedule_id: str, search_fn: Callable[[str], Awaitable[Any]]) -> dict | None:
    """Run a single scheduled search."""
    if schedule_id not in _schedules:
        return None
    sched = _schedules[schedule_id]
    if not sched["enabled"]:
        return None

    try:
        result = await search_fn(sched["name"])
        sched["last_run"] = datetime.utcnow().isoformat()
        sched["run_count"] = sched.get("run_count", 0) + 1
        sched["last_result"] = {
            "success": True,
            "results_count": len(result) if isinstance(result, list) else 0,
        }
        _save_schedules()
        return sched["last_result"]
    except Exception as e:
        sched["last_run"] = datetime.utcnow().isoformat()
        sched["run_count"] = sched.get("run_count", 0) + 1
        sched["last_result"] = {"success": False, "error": str(e)}
        _save_schedules()
        return sched["last_result"]


def get_schedule_status(schedule_id: str) -> dict | None:
    """Get status of a scheduled search."""
    _load_schedules()
    return _schedules.get(schedule_id)
