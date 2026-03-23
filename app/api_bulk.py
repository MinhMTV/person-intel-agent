"""Bulk search functionality — CSV upload for multiple person searches."""

from __future__ import annotations

import csv
import asyncio
from datetime import datetime
from pathlib import Path
from typing import List
from fastapi import APIRouter, UploadFile, File
from pydantic import BaseModel

from app.models import PersonQuery, PersonDossier, Location

router = APIRouter(prefix="/api/bulk", tags=["bulk"])


class BulkSearchResult(BaseModel):
    row: int
    name: str
    status: str  # "success", "error", "in_progress"
    dossier_id: str | None = None
    results_count: int = 0
    error: str | None = None
    timestamp: datetime = None

    def __init__(self, **data):
        if 'timestamp' not in data or data['timestamp'] is None:
            data['timestamp'] = datetime.utcnow()
        super().__init__(**data)


class BulkSearchJob(BaseModel):
    id: str
    status: str  # "pending", "running", "completed", "failed"
    total: int
    completed: int = 0
    failed: int = 0
    results: List[BulkSearchResult] = []
    started_at: datetime | None = None
    completed_at: datetime | None = None


# In-memory job store (use Redis in production)
_bulk_jobs: dict[str, BulkSearchJob] = {}


def _get_web_state():
    """Lazy import to avoid circular imports."""
    from app.web import _run_all_scanners, _dossiers, _search_history
    return _run_all_scanners, _dossiers, _search_history


@router.post("/upload")
async def bulk_upload(file: UploadFile = File(...)):
    """Upload CSV with person data and start bulk search."""
    if not file.filename.endswith('.csv'):
        return {"error": "Only CSV files supported"}
    
    content = await file.read()
    rows = list(csv.DictReader(content.decode('utf-8').splitlines()))
    
    if len(rows) > 100:
        return {"error": "Max 100 rows allowed"}
    
    job_id = f"bulk_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{len(rows)}"
    job = BulkSearchJob(id=job_id, status="pending", total=len(rows))
    _bulk_jobs[job_id] = job
    
    asyncio.create_task(_process_bulk_job(job_id, rows))
    
    return {"job_id": job_id, "total": len(rows), "status": "started"}


async def _process_bulk_job(job_id: str, rows: List[dict]):
    _run_all_scanners, _dossiers, _search_history = _get_web_state()
    job = _bulk_jobs[job_id]
    job.status = "running"
    job.started_at = datetime.utcnow()
    
    for i, row in enumerate(rows, 1):
        name = row.get('name', '').strip()
        if not name:
            job.results.append(BulkSearchResult(row=i, name="", status="error", error="No name"))
            continue
        
        try:
            parts = name.split()
            query = PersonQuery(
                full_name=name,
                first_name=parts[0] if parts else None,
                last_name=parts[-1] if len(parts) > 1 else None,
                nicknames=[n.strip() for n in row.get('nicknames', '').split(',') if n.strip()],
                locations=[Location(raw=l.strip()) for l in row.get('locations', '').split(',') if l.strip()],
                usernames=[u.strip() for u in row.get('usernames', '').split(',') if u.strip()],
                emails=[e.strip() for e in row.get('email', '').split(',') if e.strip()],
            )
            
            dossier = await asyncio.wait_for(_run_all_scanners(query), timeout=180)
            
            dossier_id = f"bulk_{job_id}_{i}"
            _dossiers[dossier_id] = dossier
            
            total = sum([
                len(dossier.social_profiles),
                len(dossier.web_results),
                len(dossier.image_matches),
                len(dossier.email_addresses)
            ])
            
            job.results.append(BulkSearchResult(row=i, name=name, status="success",
                dossier_id=dossier_id, results_count=total))
            job.completed += 1
            await asyncio.sleep(2)
            
        except Exception as e:
            job.results.append(BulkSearchResult(row=i, name=name, status="error", error=str(e)[:100]))
    
    job.status = "completed"
    job.completed_at = datetime.utcnow()


@router.get("/status/{job_id}")
async def bulk_status(job_id: str):
    job = _bulk_jobs.get(job_id)
    if not job:
        return {"error": "Job not found"}
    return {"id": job.id, "status": job.status, "progress": job.completed}


@router.get("/jobs")
async def list_bulk_jobs():
    return {"jobs": [{"id": j.id, "status": j.status, "progress": j.completed} for j in _bulk_jobs.values()]}
