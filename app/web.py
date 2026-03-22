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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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
    version="0.2.0",
)

# Serve static assets
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# In-memory dossier store (swap for Redis/DB in production)
_dossiers: dict[str, PersonDossier] = {}


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
    """Download a dossier in JSON or Markdown format."""
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
    else:
        content = dossier.summary()
        return HTMLResponse(
            content=f"<pre>{content}</pre>",
            headers={"Content-Disposition": f'attachment; filename="{filename}.md"'},
        )


# ---------------------------------------------------------------------------
# Scanner runner (shared with CLI)
# ---------------------------------------------------------------------------

async def _run_all_scanners(query: PersonQuery) -> PersonDossier:
    """Run all available scanners and aggregate results."""
    from app.scanners.social import SocialScanner
    from app.scanners.web import WebScanner
    from app.scanners.image import ImageScanner
    from app.scanners.email import EmailScanner
    from app.scanners.professional import ProfessionalScanner
    from app.scanners.advanced_image import AdvancedImageScanner

    scanners = [
        ("social", SocialScanner()),
        ("web", WebScanner()),
        ("email", EmailScanner()),
        ("image", ImageScanner()),
        ("advanced_image", AdvancedImageScanner()),
        ("professional", ProfessionalScanner(headless=True)),
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
                    # Classify by source
                    if r.source == Source.EMAIL or r.source == Source.BREACH:
                        dossier.email_addresses.append(r.url.replace("mailto:", ""))
                    elif r.source in (Source.LINKEDIN, Source.XING):
                        dossier.professional.append(r)
                    else:
                        dossier.web_results.append(r)
        except Exception as exc:
            print(f"Scanner '{label}' failed: {exc}")

    dossier.total_sources_checked = (
        len(dossier.web_results)
        + len(dossier.social_profiles)
        + len(dossier.email_addresses)
    )
    # Simple confidence aggregation
    total = dossier.total_sources_checked + len(dossier.image_matches)
    if total > 0:
        dossier.confidence_score = min(1.0, total / 20)

    return dossier
