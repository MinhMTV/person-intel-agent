"""Shared API dependencies and helpers."""

from __future__ import annotations

import ipaddress

from fastapi import HTTPException, Request, UploadFile

from app.container import Container
from app.domain.investigation import Investigation


def get_container(request: Request) -> Container:
    return request.app.state.container


def load_investigation(container: Container, investigation_id: str, *, with_result: bool = True) -> Investigation:
    if not investigation_id.isalnum() or len(investigation_id) > 64:
        raise HTTPException(status_code=404, detail="Investigation not found")
    inv = container.repo.get(investigation_id, with_result=with_result)
    if inv is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return inv


def ensure_not_running(container: Container, inv: Investigation) -> None:
    if inv.status.is_active or container.runner.is_running(inv.id):
        raise HTTPException(status_code=409, detail="The investigation is running; wait until it finishes.")


async def read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """Read an upload with a hard size cap (never trusts Content-Length)."""
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds the {max_bytes // (1024 * 1024)} MB limit.")
    return data


def is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if host in ("testclient", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
