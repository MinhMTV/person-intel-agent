"""FastAPI Web Application for Person Intelligence Agent.

Provides a REST API and web UI for running OSINT searches,
viewing dossiers, and managing platform logins.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.models import (
    Confidence,
    ImageMatch,
    Location,
    PersonDossier,
    PersonQuery,
    SearchResult,
    SocialProfile,
    Source,
)

# Import filter router
from app.api_filters import router as filter_router
from app.api_bulk import router as bulk_router
from app.api_timeline import router as timeline_router
from app.api_scanners import router as scanners_router
from app.api_suggest import router as suggest_router

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = BASE_DIR.parent / "output"
UPLOAD_DIR = OUTPUT_DIR / "uploads"
OUTPUT_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(
    title="Person Intel Agent",
    description="Automated OSINT Dossier Generator — Web Interface",
    version="0.3.0",
)

# Include routers
app.include_router(filter_router)
app.include_router(bulk_router)
app.include_router(timeline_router)
app.include_router(scanners_router)
app.include_router(suggest_router)

# Serve static assets
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# In-memory dossier store (swap for Redis/DB in production)
_dossiers: dict[str, PersonDossier] = {}
_search_history: list[dict] = []  # Recent search history
_activity_log: list[dict] = []  # Activity log (max 200)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main search interface."""
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text(), status_code=200)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.post("/api/search")
async def api_search(
    name: str = Form(...),
    locations: str = Form(""),
    usernames: str = Form(""),
    nicknames: str = Form(""),
    email: str = Form(""),
    photo: Optional[UploadFile] = File(None),
):
    """Run an OSINT search and return a dossier ID + JSON summary.

    Form fields:
        name:       Full name (required)
        locations:  Comma-separated locations
        usernames:  Comma-separated usernames
        nicknames:  Comma-separated nicknames
        email:      Comma-separated email addresses
        photo:      Optional photo file for face recognition
    """
    # Parse comma-separated fields
    loc_list = [l.strip() for l in locations.split(",") if l.strip()]
    user_list = [u.strip() for u in usernames.split(",") if u.strip()]
    nick_list = [n.strip() for n in nicknames.split(",") if n.strip()]
    email_list = [e.strip() for e in email.split(",") if e.strip()]

    # Save uploaded photo
    photo_path: Optional[str] = None
    if photo and photo.filename:
        ext = Path(photo.filename).suffix or ".jpg"
        fname = f"{uuid.uuid4().hex}{ext}"
        dest = UPLOAD_DIR / fname
        with open(dest, "wb") as f:
            shutil.copyfileobj(photo.file, f)
        photo_path = str(dest)

    # Build query
    parts = name.strip().split()
    query = PersonQuery(
        full_name=name.strip(),
        first_name=parts[0] if parts else None,
        last_name=parts[-1] if len(parts) > 1 else None,
        nicknames=nick_list,
        locations=[Location(raw=loc) for loc in loc_list],
        usernames=user_list,
        emails=email_list,
        photo_path=photo_path,
    )

    # Run scanners
    dossier = await _run_all_scanners(query)

    # Store dossier
    dossier_id = uuid.uuid4().hex[:12]
    _dossiers[dossier_id] = dossier

    # Add to search history
    _search_history.insert(0, {
        "id": dossier_id,
        "name": query.full_name,
        "timestamp": datetime.utcnow().isoformat(),
        "results": {
            "social_profiles": len(dossier.social_profiles),
            "web_results": len(dossier.web_results),
            "email_addresses": len(dossier.email_addresses),
            "image_matches": len(dossier.image_matches),
        },
    })
    # Keep only last 50 searches
    _search_history[:] = _search_history[:50]
    _log_activity(dossier_id, "search", query.full_name)

    return {
        "id": dossier_id,
        "query": {"name": query.full_name, "locations": loc_list},
        "results": {
            "social_profiles": len(dossier.social_profiles),
            "web_results": len(dossier.web_results),
            "email_addresses": len(dossier.email_addresses),
            "image_matches": len(dossier.image_matches),
        },
        "scanners_used": dossier.scanners_used,
        "dossier": json.loads(dossier.model_dump_json()),
    }


@app.get("/api/dossier/{dossier_id}")
async def api_dossier(dossier_id: str):
    """Return a stored dossier as JSON."""
    dossier = _dossiers.get(dossier_id)
    if not dossier:
        raise HTTPException(status_code=404, detail="Dossier not found")
    return JSONResponse(content=json.loads(dossier.model_dump_json()))


@app.get("/api/history")
async def api_history(limit: int = 20):
    """Return search history with dossier summaries."""
    enriched = []
    for entry in _search_history[:limit]:
        dossier_id = entry.get("id")
        dossier = _dossiers.get(dossier_id) if dossier_id else None
        enriched_entry = dict(entry)
        if dossier:
            enriched_entry["confidence"] = dossier.confidence_score
            enriched_entry["results"] = {
                "social_profiles": len(dossier.social_profiles),
                "web_results": len(dossier.web_results),
                "image_matches": len(dossier.image_matches),
                "email_addresses": len(dossier.email_addresses),
                "professional": len(dossier.professional),
            }
            enriched_entry["scanners_used"] = dossier.scanners_used
            # Best social profile
            if dossier.social_profiles:
                best = max(dossier.social_profiles, key=lambda p: 1 if p.confidence.value == "high" else 0)
                enriched_entry["top_platform"] = best.platform
        enriched.append(enriched_entry)

    # Sort: pinned first
    enriched.sort(key=lambda x: (0 if x.get("pinned") else 1), reverse=False)
    return {"history": enriched}


@app.post("/api/history/pin/{dossier_id}")
async def api_pin_search(dossier_id: str):
    """Pin/unpin a search in history."""
    for entry in _search_history:
        if entry.get("id") == dossier_id:
            entry["pinned"] = not entry.get("pinned", False)
            _log_activity(dossier_id, "pin", "pinned" if entry["pinned"] else "unpinned")
            return {"id": dossier_id, "pinned": entry["pinned"]}
    raise HTTPException(status_code=404, detail="Search not found")


@app.post("/api/history/notes/{dossier_id}")
async def api_add_notes(dossier_id: str, body: dict = Body(...)):
    """Add/update notes for a search."""
    notes = body.get("notes", "")
    for entry in _search_history:
        if entry.get("id") == dossier_id:
            entry["notes"] = notes
            return {"id": dossier_id, "notes": notes}
    raise HTTPException(status_code=404, detail="Search not found")


@app.post("/api/history/tags/{dossier_id}")
async def api_add_tag(dossier_id: str, body: dict = Body(...)):
    """Add/remove tags for a search."""
    tag = body.get("tag", "").strip().lower()
    if not tag:
        raise HTTPException(status_code=400, detail="Tag required")
    for entry in _search_history:
        if entry.get("id") == dossier_id:
            tags = set(entry.get("tags", []))
            if tag in tags:
                tags.discard(tag)
            else:
                tags.add(tag)
            entry["tags"] = sorted(tags)
            _log_activity(dossier_id, "tag", f"{tag} {'removed' if tag not in tags else 'added'}")
            return {"id": dossier_id, "tags": entry["tags"]}
    raise HTTPException(status_code=404, detail="Search not found")


@app.post("/api/history/tags/batch")
async def api_batch_tag(body: dict = Body(...)):
    """Add a tag to multiple searches at once."""
    tag = body.get("tag", "").strip().lower()
    dossier_ids = body.get("dossier_ids", [])
    if not tag or not dossier_ids:
        raise HTTPException(status_code=400, detail="Tag and dossier_ids required")
    tagged = 0
    for entry in _search_history:
        if entry.get("id") in dossier_ids:
            tags = set(entry.get("tags", []))
            tags.add(tag)
            entry["tags"] = sorted(tags)
            _log_activity(entry["id"], "tag", f"{tag} added (batch)")
            tagged += 1
    return {"tag": tag, "tagged": tagged}


@app.get("/api/history/tags")
async def api_all_tags():
    """Get all unique tags with counts."""
    tag_counts: dict[str, int] = {}
    for entry in _search_history:
        for t in entry.get("tags", []):
            tag_counts[t] = tag_counts.get(t, 0) + 1
    return {"tags": tag_counts}


def _log_activity(dossier_id: str, action: str, detail: str = ""):
    """Log an activity event."""
    import datetime
    _activity_log.insert(0, {
        "dossier_id": dossier_id,
        "action": action,
        "detail": detail,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    })
    # Keep only last 200
    if len(_activity_log) > 200:
        _activity_log.pop()


@app.get("/api/activity")
async def api_activity(limit: int = 50):
    """Get recent activity log."""
    return {"activity": _activity_log[:limit]}


@app.get("/api/rate-stats")
async def api_rate_stats():
    """Get API request statistics per scanner."""
    from app.rate_tracker import tracker
    return tracker.get_stats()


@app.post("/api/cache/clear")
async def api_cache_clear(body: dict = Body(default={})):
    """Clear scanner cache."""
    from app.cache import clear_cache
    query_name = body.get("query")
    cleared = clear_cache(query_name)
    return {"cleared": cleared}


@app.get("/api/dossier/{dossier_id}/risk")
async def api_dossier_risk(dossier_id: str):
    """Calculate risk score for a dossier."""
    dossier = _dossiers.get(dossier_id)
    if not dossier:
        raise HTTPException(status_code=404, detail="Dossier not found")
    from app.analysis.risk_score import calculate_risk_score
    risk = calculate_risk_score(dossier.model_dump())
    return {
        "dossier_id": dossier_id,
        "name": dossier.query.full_name,
        "score": risk.score,
        "level": risk.level,
        "factors": risk.factors,
        "recommendations": risk.recommendations,
    }


@app.get("/api/dossier/{dossier_id}/diff/{other_id}")
async def api_dossier_diff(dossier_id: str, other_id: str):
    """Compare two dossiers and show differences."""
    d1 = _dossiers.get(dossier_id)
    d2 = _dossiers.get(other_id)
    if not d1 or not d2:
        raise HTTPException(status_code=404, detail="Dossier not found")
    from app.diff import diff_dossiers, diff_summary
    diff = diff_dossiers(d1.model_dump(), d2.model_dump())
    diff["summary"] = diff_summary(diff)
    diff["dossier_a"] = {"id": dossier_id, "name": d1.query.full_name}
    diff["dossier_b"] = {"id": other_id, "name": d2.query.full_name}
    return diff


@app.get("/api/schedules")
async def api_list_schedules():
    """List all scheduled searches."""
    from app.scheduled import get_schedules
    return {"schedules": get_schedules()}


@app.post("/api/schedules")
async def api_add_schedule(body: dict = Body(...)):
    """Add a scheduled search."""
    from app.scheduled import add_schedule
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    interval = body.get("interval_hours", 24)
    sid = add_schedule(name, interval_hours=interval)
    return {"id": sid, "name": name}


@app.delete("/api/schedules/{sid}")
async def api_delete_schedule(sid: str):
    """Delete a scheduled search."""
    from app.scheduled import remove_schedule
    ok = remove_schedule(sid)
    if not ok:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"deleted": sid}


@app.post("/api/schedules/{sid}/toggle")
async def api_toggle_schedule(sid: str):
    """Toggle a scheduled search on/off."""
    from app.scheduled import toggle_schedule
    enabled = toggle_schedule(sid)
    if enabled is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"id": sid, "enabled": enabled}


@app.get("/api/history/export")
async def api_history_export(fmt: str = "json"):
    """Export search history as JSON or CSV."""
    import csv
    import io

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["id", "name", "timestamp", "results"])
        writer.writeheader()
        for h in _search_history:
            row = dict(h)
            if "results" in row and isinstance(row["results"], dict):
                row["results"] = "; ".join(f"{k}={v}" for k, v in row["results"].items())
            writer.writerow(row)
        return HTMLResponse(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="search_history.csv"'},
        )
    else:
        return JSONResponse(
            content=_search_history,
            headers={"Content-Disposition": 'attachment; filename="search_history.json"'},
        )


@app.get("/api/dossiers/stats")
async def api_dossier_stats():
    """Get stats across all dossiers."""
    if not _dossiers:
        return {"total": 0, "stats": {}}
    total = len(_dossiers)
    total_social = sum(len(d.social_profiles) for d in _dossiers.values())
    total_web = sum(len(d.web_results) for d in _dossiers.values())
    total_images = sum(len(d.image_matches) for d in _dossiers.values())
    total_emails = sum(len(d.email_addresses) for d in _dossiers.values())
    total_professional = sum(len(d.professional) for d in _dossiers.values())
    avg_confidence = sum(d.confidence_score for d in _dossiers.values()) / total
    platforms = {}
    for d in _dossiers.values():
        for sp in d.social_profiles:
            platforms[sp.platform] = platforms.get(sp.platform, 0) + 1
    top_platforms = sorted(platforms.items(), key=lambda x: x[1], reverse=True)[:10]
    return {
        "total": total,
        "stats": {
            "total_social_profiles": total_social,
            "total_web_results": total_web,
            "total_image_matches": total_images,
            "total_email_addresses": total_emails,
            "total_professional": total_professional,
            "avg_confidence": round(avg_confidence, 3),
            "top_platforms": dict(top_platforms),
        },
        "pinned": sum(1 for e in _search_history if e.get("pinned")),
        "tagged": sum(1 for e in _search_history if e.get("tags")),
    }


@app.get("/api/dossiers/search")
async def api_dossier_search(q: str = "", limit: int = 20):
    """Full-text search across all dossier results."""
    if not q or len(q) < 2:
        return {"results": [], "query": q}
    q_lower = q.lower()
    hits = []
    for did, d in _dossiers.items():
        matches = []
        for sp in d.social_profiles:
            if q_lower in sp.platform.lower() or q_lower in (sp.url or "").lower():
                matches.append({"type": "social", "platform": sp.platform, "url": sp.url})
        for wr in d.web_results:
            if q_lower in wr.title.lower() or q_lower in wr.url.lower() or q_lower in (wr.snippet or "").lower():
                matches.append({"type": "web", "title": wr.title, "url": wr.url})
        for em in d.email_addresses:
            if q_lower in em.email.lower():
                matches.append({"type": "email", "email": em.email})
        for pr in d.professional:
            if q_lower in pr.platform.lower() or q_lower in (pr.title or "").lower():
                matches.append({"type": "professional", "platform": pr.platform, "title": pr.title})
        if q_lower in d.query.full_name.lower():
            matches.insert(0, {"type": "name", "name": d.query.full_name})
        if matches:
            hits.append({"dossier_id": did, "name": d.query.full_name, "confidence": d.confidence_score, "matches": matches[:5]})
    hits.sort(key=lambda h: h["confidence"], reverse=True)
    return {"results": hits[:limit], "query": q, "total_hits": len(hits)}


@app.get("/api/dossiers/export-all")
async def api_export_all(fmt: str = "json"):
    """Export all dossiers as a single JSON or concatenated Markdown."""
    import io

    if not _dossiers:
        return {"error": "No dossiers to export"}
    _log_activity("all", "export-all", fmt)

    if fmt == "json":
        all_data = {}
        for did, d in _dossiers.items():
            all_data[did] = {
                "name": d.query.full_name,
                "confidence": d.confidence_score,
                "social_profiles": len(d.social_profiles),
                "web_results": len(d.web_results),
                "image_matches": len(d.image_matches),
                "email_addresses": len(d.email_addresses),
                "dossier": json.loads(d.model_dump_json()),
            }
        return JSONResponse(
            content=all_data,
            headers={"Content-Disposition": 'attachment; filename="all_dossiers.json"'},
        )
    else:
        parts = []
        for did, d in _dossiers.items():
            parts.append(f"# Dossier: {d.query.full_name}\n")
            parts.append(f"ID: {did}\nConfidence: {d.confidence_score:.1%}\n\n")
            parts.append(d.summary())
            parts.append("\n---\n\n")
        content = "\n".join(parts)
        return HTMLResponse(
            content=f"<pre>{content}</pre>",
            headers={"Content-Disposition": 'attachment; filename="all_dossiers.md"'},
        )


@app.post("/api/login/{platform}")
async def api_login(platform: str):
    """Trigger browser-based login for LinkedIn or Xing.

    Opens a visible Chromium window for manual credential entry,
    then saves session cookies for future authenticated scraping.
    """
    if platform not in ("linkedin", "xing"):
        raise HTTPException(status_code=400, detail="Platform must be 'linkedin' or 'xing'")

    from app.scanners.professional import LinkedInScraper, XingScraper

    async def _do_login():
        if platform == "linkedin":
            scraper = LinkedInScraper(headless=False)
            return await scraper.login_and_save_cookies()
        else:
            scraper = XingScraper(headless=False)
            return await scraper.login_and_save_cookies()

    try:
        ok = await asyncio.wait_for(_do_login(), timeout=120)
        return {"platform": platform, "success": ok}
    except asyncio.TimeoutError:
        return {"platform": platform, "success": False, "error": "Login timed out (2 min)"}
    except Exception as e:
        return {"platform": platform, "success": False, "error": str(e)}


@app.get("/api/dossier/{dossier_id}/download")
async def api_dossier_download(dossier_id: str, fmt: str = "json"):
    """Download a dossier in JSON, Markdown, or HTML format."""
    dossier = _dossiers.get(dossier_id)
    if not dossier:
        raise HTTPException(status_code=404, detail="Dossier not found")

    filename = dossier.query.full_name.replace(" ", "_").lower()

    if fmt == "json":
        content = dossier.model_dump_json(indent=2)
        return JSONResponse(
            content=json.loads(content),
            headers={"Content-Disposition": f'attachment; filename="{filename}.json"'},
        )
    elif fmt == "html":
        from app.report_html import generate_html_report
        html_content = generate_html_report(dossier, dossier_id)
        return HTMLResponse(
            content=html_content,
            headers={"Content-Disposition": f'attachment; filename="{filename}_dossier.html"'},
        )
    elif fmt == "pdf":
        from app.report_pdf import generate_pdf
        pdf_bytes = generate_pdf(dossier)
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}_dossier.pdf"'},
        )
    else:
        content = dossier.summary()
        return HTMLResponse(
            content=f"<pre>{content}</pre>",
            headers={"Content-Disposition": f'attachment; filename="{filename}.md"'},
        )


@app.get("/api/search/stream")
async def api_search_stream(
    name: str,
    locations: str = "",
    usernames: str = "",
    nicknames: str = "",
    email: str = "",
):
    """Run a search with real-time SSE progress updates.

    Returns a Server-Sent Events stream with progress for each scanner.
    """
    async def event_generator():
        # Parse fields
        loc_list = [l.strip() for l in locations.split(",") if l.strip()]
        user_list = [u.strip() for u in usernames.split(",") if u.strip()]
        nick_list = [n.strip() for n in nicknames.split(",") if n.strip()]
        email_list = [e.strip() for e in email.split(",") if e.strip()]

        parts = name.strip().split()
        query = PersonQuery(
            full_name=name.strip(),
            first_name=parts[0] if parts else None,
            last_name=parts[-1] if len(parts) > 1 else None,
            nicknames=nick_list,
            locations=[Location(raw=loc) for loc in loc_list],
            usernames=user_list,
            emails=email_list,
        )

        # Import scanners
        from app.scanners.social import SocialScanner
        from app.scanners.web import WebScanner
        from app.scanners.email import EmailScanner
        from app.scanners.deep_social import DeepSocialScanner
        from app.scanners.professional_intel import ProfessionalIntelScanner
        from app.scanners.public_records import PublicRecordsScanner
        from app.scanners.data_enrichment import DataEnrichmentScanner

        scanners = [
            ("social", SocialScanner()),
            ("web", WebScanner()),
            ("email", EmailScanner()),
            ("deep_social", DeepSocialScanner()),
            ("professional_intel", ProfessionalIntelScanner()),
            ("public_records", PublicRecordsScanner()),
            ("data_enrichment", DataEnrichmentScanner()),
        ]

        dossier = PersonDossier(query=query)
        total = len(scanners)

        yield f"data: {json.dumps({'type': 'start', 'total': total, 'name': query.full_name})}\n\n"

        for i, (label, scanner) in enumerate(scanners):
            yield f"data: {json.dumps({'type': 'progress', 'scanner': label, 'current': i+1, 'total': total})}\n\n"

            try:
                results = await scanner.scan(query)
                dossier.scanners_used.append(label)

                count = 0
                for r in results:
                    count += 1
                    if isinstance(r, SocialProfile):
                        dossier.social_profiles.append(r)
                    elif isinstance(r, ImageMatch):
                        dossier.image_matches.append(r)
                    elif isinstance(r, SearchResult):
                        if r.source == Source.EMAIL or r.source == Source.BREACH:
                            dossier.email_addresses.append(r.url.replace("mailto:", ""))
                        elif r.source in (Source.LINKEDIN, Source.XING):
                            dossier.professional.append(r)
                        elif r.source == Source.ACADEMIC:
                            dossier.academic.append(r)
                        else:
                            dossier.web_results.append(r)

                yield f"data: {json.dumps({'type': 'scanner_done', 'scanner': label, 'results': count})}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'type': 'scanner_error', 'scanner': label, 'error': str(exc)})}\n\n"

        # Finalize
        dossier.total_sources_checked = (
            len(dossier.web_results) + len(dossier.social_profiles)
            + len(dossier.email_addresses) + len(dossier.professional) + len(dossier.academic)
        )
        total_results = dossier.total_sources_checked + len(dossier.image_matches)
        if total_results > 0:
            dossier.confidence_score = min(1.0, total_results / 20)

        # Store
        dossier_id = uuid.uuid4().hex[:12]
        _dossiers[dossier_id] = dossier

        summary = {
            "social_profiles": len(dossier.social_profiles),
            "web_results": len(dossier.web_results),
            "email_addresses": len(dossier.email_addresses),
            "image_matches": len(dossier.image_matches),
            "professional": len(dossier.professional),
            "academic": len(dossier.academic),
        }

        yield f"data: {json.dumps({'type': 'done', 'id': dossier_id, 'results': summary})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Scanner runner (shared with CLI)
# ---------------------------------------------------------------------------

async def _run_all_scanners(query: PersonQuery) -> PersonDossier:
    """Run all available scanners and aggregate results with dedup + scoring."""
    from app.scanners.social import SocialScanner
    from app.scanners.web import WebScanner
    from app.scanners.image import ImageScanner
    from app.scanners.email import EmailScanner
    from app.scanners.professional import ProfessionalScanner
    from app.scanners.advanced_image import AdvancedImageScanner
    from app.scanners.reverse_image import ReverseImageScanner
    from app.scanners.deep_social import DeepSocialScanner
    from app.scanners.professional_intel import ProfessionalIntelScanner
    from app.scanners.public_records import PublicRecordsScanner
    from app.scanners.data_enrichment import DataEnrichmentScanner
    from app.analysis.dedup import dedup_all
    from app.analysis.scoring import apply_confidence_scores

    import asyncio

    scanners = [
        ("social", SocialScanner()),
        ("web", WebScanner()),
        ("email", EmailScanner()),
        ("image", ImageScanner()),
        ("advanced_image", AdvancedImageScanner()),
        ("reverse_image", ReverseImageScanner()),
        ("deep_social", DeepSocialScanner()),
        ("professional", ProfessionalScanner(headless=True)),
        ("professional_intel", ProfessionalIntelScanner()),
        ("public_records", PublicRecordsScanner()),
        ("data_enrichment", DataEnrichmentScanner()),
    ]

    dossier = PersonDossier(query=query)

    async def _run_one(label, scanner):
        """Run a single scanner with caching + rate tracking, return (label, results)."""
        from app.cache import get_cached, set_cached
        from app.rate_tracker import tracker
        import time

        cached = get_cached(query.full_name, label, ttl=1800)
        if cached is not None:
            try:
                return label, [SocialProfile(**r) if "platform" in r and "url" in r else
                              ImageMatch(**r) if "image_url" in r else
                              SearchResult(**r) for r in cached]
            except Exception:
                pass

        start = time.time()
        try:
            results = await asyncio.wait_for(scanner.scan(query), timeout=30)
            duration_ms = (time.time() - start) * 1000
            tracker.record(label, duration_ms=duration_ms)
            try:
                serializable = [r.model_dump() if hasattr(r, "model_dump") else r.dict() if hasattr(r, "dict") else str(r) for r in results]
                set_cached(query.full_name, label, serializable)
            except Exception:
                pass
            return label, results
        except Exception as exc:
            duration_ms = (time.time() - start) * 1000
            tracker.record(label, duration_ms=duration_ms, error=True)
            print(f"Scanner '{label}' failed: {exc}")
            return label, []

    tasks = [_run_one(label, scanner) for label, scanner in scanners]
    completed = await asyncio.gather(*tasks, return_exceptions=True)

    for result in completed:
        if isinstance(result, Exception):
            continue
        label, results = result
        dossier.scanners_used.append(label)
        for r in results:
            if isinstance(r, SocialProfile):
                dossier.social_profiles.append(r)
            elif isinstance(r, ImageMatch):
                dossier.image_matches.append(r)
            elif isinstance(r, SearchResult):
                if r.source == Source.EMAIL or r.source == Source.BREACH:
                    dossier.email_addresses.append(r.url.replace("mailto:", ""))
                elif r.source in (Source.LINKEDIN, Source.XING):
                    dossier.professional.append(r)
                elif r.source == Source.ACADEMIC:
                    dossier.academic.append(r)
                else:
                    dossier.web_results.append(r)

    # Deduplicate results
    deduped = dedup_all(
        social=dossier.social_profiles,
        web=dossier.web_results,
        images=dossier.image_matches,
        emails=dossier.email_addresses,
        professional=dossier.professional,
        academic=dossier.academic,
    )
    dossier.social_profiles = deduped["social_profiles"]
    dossier.web_results = deduped["web_results"]
    dossier.image_matches = deduped["image_matches"]
    dossier.email_addresses = deduped["email_addresses"]
    dossier.professional = deduped["professional"]
    dossier.academic = deduped["academic"]

    # Apply confidence scores
    scored = apply_confidence_scores(
        social_profiles=dossier.social_profiles,
        web_results=dossier.web_results,
        image_matches=dossier.image_matches,
        query=query,
    )
    dossier.confidence_score = scored["overall_confidence"]

    dossier.total_sources_checked = (
        len(dossier.web_results)
        + len(dossier.social_profiles)
        + len(dossier.email_addresses)
        + len(dossier.professional)
        + len(dossier.academic)
    )

    return dossier
