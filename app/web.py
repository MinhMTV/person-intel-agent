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

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
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

# Serve static assets
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# In-memory dossier store (swap for Redis/DB in production)
_dossiers: dict[str, PersonDossier] = {}
_search_history: list[dict] = []  # Recent search history


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
    """Return search history."""
    return {"history": _search_history[:limit]}


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

    for label, scanner in scanners:
        try:
            results = await scanner.scan(query)
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
        except Exception as exc:
            print(f"Scanner '{label}' failed: {exc}")

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
