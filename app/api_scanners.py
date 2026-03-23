"""Scanner Status API — health check and metadata for all scanners."""

from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter

router = APIRouter(prefix="/api/scanners", tags=["scanners"])

SCANNER_REGISTRY = {
    "social": {"name": "Social Media", "description": "Username checks across 20+ platforms", "type": "async", "requires_auth": False},
    "web": {"name": "Web Search", "description": "DuckDuckGo + Bing + Yandex with fuzzy matching", "type": "async", "requires_auth": False},
    "email": {"name": "Email Harvesting", "description": "theHarvester wrapper for email discovery", "type": "async", "requires_auth": False},
    "image": {"name": "Image Scanner", "description": "Face recognition + multi-source image discovery", "type": "async", "requires_auth": False},
    "advanced_image": {"name": "Advanced Image", "description": "Enhanced face recognition with quality assessment", "type": "async", "requires_auth": False},
    "reverse_image": {"name": "Reverse Image", "description": "Yandex + TinEye + Wikidata reverse image search", "type": "async", "requires_auth": False},
    "deep_social": {"name": "Deep Social", "description": "Instagram, GitHub deep, Reddit, StackOverflow", "type": "async", "requires_auth": False},
    "professional": {"name": "Professional", "description": "LinkedIn + Xing with Playwright session management", "type": "browser", "requires_auth": True},
    "professional_intel": {"name": "Professional Intel", "description": "Scholar + ORCID + Patents + Conferences", "type": "async", "requires_auth": False},
    "public_records": {"name": "Public Records", "description": "WHOIS + DNS + SSL + Domain intelligence", "type": "async", "requires_auth": False},
    "data_enrichment": {"name": "Data Enrichment", "description": "Phone + Email verification + Cross-referencing", "type": "async", "requires_auth": False},
}


@router.get("/status")
async def scanner_status():
    """Get status of all available scanners."""
    scanners = []
    for scanner_id, info in SCANNER_REGISTRY.items():
        status = "available"
        try:
            # Try to import the scanner module
            module_path = f"app.scanners.{scanner_id}"
            if scanner_id == "professional":
                from app.scanners.professional import ProfessionalScanner
            elif scanner_id == "advanced_image":
                from app.scanners.advanced_image import AdvancedImageScanner
            elif scanner_id == "reverse_image":
                from app.scanners.reverse_image import ReverseImageScanner
            elif scanner_id == "deep_social":
                from app.scanners.deep_social import DeepSocialScanner
            elif scanner_id == "professional_intel":
                from app.scanners.professional_intel import ProfessionalIntelScanner
            elif scanner_id == "public_records":
                from app.scanners.public_records import PublicRecordsScanner
            elif scanner_id == "data_enrichment":
                from app.scanners.data_enrichment import DataEnrichmentScanner
            else:
                __import__(module_path)
        except ImportError as e:
            status = f"import_error: {str(e)[:80]}"
        except Exception as e:
            status = f"error: {str(e)[:80]}"

        scanners.append({
            "id": scanner_id,
            "name": info["name"],
            "description": info["description"],
            "type": info["type"],
            "requires_auth": info["requires_auth"],
            "status": status,
        })

    return {
        "scanners": scanners,
        "total": len(scanners),
        "available": sum(1 for s in scanners if s["status"] == "available"),
        "checked_at": datetime.utcnow().isoformat(),
    }


@router.get("/list")
async def scanner_list():
    """Simple list of scanner IDs and names."""
    return {
        "scanners": [
            {"id": sid, "name": info["name"], "type": info["type"]}
            for sid, info in SCANNER_REGISTRY.items()
        ]
    }
