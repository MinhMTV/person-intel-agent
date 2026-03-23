# Person Intelligence Agent (OSINT-Dossier-Generator)

## Ziel
Automatisierte Personenrecherche: Gib einen Namen ein, bekomme ein strukturiertes Dossier mit allen öffentlich verfügbaren Informationen.

**Ethik-Hinweis:** Nur für öffentlich zugängliche Informationen. Kein Stalking, keine Privatsphäre-Verletzung. Für Journalisten, Recruiter, Due Diligence, Wiederfinden von Kontakten.

---

## Use Cases

### 🎯 Kern-Use Cases (MVP)
1. **Vollständige Personenrecherche** — Name → Social Networks, LinkedIn, Xing, Web
2. **Google-Dossier** — Verschiedene Namensvarianten + Standort-Filterung
3. **Standort-Intelligenz** — "Oberhausen" → NRW-Suche zuerst, dann erweitern
4. **Bild-Suche (Similarity)** — Fotos von Schulzeit etc. finden, Unähnliche filtern
5. **Flexible Filter** — Länder, Kontinente, Usernames, Spitznamen, Alter

### 🔥 Erweiterte Use Cases
6. **E-Mail-Harvesting** — Bekannte E-Mails finden + verifizieren (theHarvester)
7. **Telefonnummern-Suche** — Öffentliche Verzeichnisse, WhatsApp-Profile
8. **Arbeitgeber-Verifizierung** — Firmenprofil + Mitarbeiter-Check
9. **Academic/Publications** — Google Scholar, ResearchGate, ORCID
10. **GitHub/Dev-Profile** — Code-Aktivität, Repos, Contributions
11. **Dating-App-Präsenz** — Öffentliche Profile (Tinder, Bumble etc.)
12. **Breach-Check** — HaveIBeenPwned, öffentliche Datenleaks (legal)
13. **Domain/Website-Besitz** — WHOIS, Domain-Registrierung
14. **Firmenregister** — Firmenbuch-Einträge, Geschäftsführer
15. **Konferenz/Podcast** — Als Speaker/Gast aufgetreten
16. **YouTube/Video-Content** — Kanäle, Interviews, Auftritte
17. **Foren/Blogs** — StackOverflow, Reddit, Medium-Aktivität
18. **Politische Spenden** — Öffentliche Spenden-Datenbanken (USA etc.)
19. **Immobilien-Besitz** — Öffentliche Grundbücher (wo verfügbar)
20. **Patente/IP** — Patentamt-Einträge

---

## Tech-Stack
- **Python 3.11+**
- **Browser Automation:** Playwright (LinkedIn, Xing, Google)
- **CLI-Tools (Wrapper):**
  - `sherlock` / `maigret` — Username-Suche
  - `theHarvester` — E-Mail/Domain-Harvesting
  - `spiderfoot` — Automatisiertes OSINT
- **Bildanalyse:** DeepFace/ArcFace (512-d), optional face_recognition/dlib (128-d), OpenCV quality scoring
- **Web Scraping:** BeautifulSoup, httpx
- **Output:** Markdown → PDF (WeasyPrint)
- **Optional:** OpenClaw Integration (Skill)

---

## Architektur

```
person-intel-agent/
├── app/
│   ├── __init__.py
│   ├── main.py              # CLI Entry Point
│   ├── config.py             # Settings + Filter
│   ├── scanners/
│   │   ├── __init__.py
│   │   ├── social.py         # Sherlock/Maigret Wrapper
│   │   ├── web.py            # Google Search + Scraping
│   │   ├── email.py          # theHarvester Wrapper
│   │   ├── image.py          # Reverse Image Search + Face Match
│   │   ├── professional.py   # LinkedIn/Xing Scraping
│   │   ├── developer.py      # GitHub/StackOverflow
│   │   ├── academic.py       # Scholar/ResearchGate
│   │   └── breach.py         # HaveIBeenPwned API
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── dedup.py          # Duplikat-Erkennung
│   │   ├── scoring.py        # Confidence-Score pro Ergebnis
│   │   └── location.py       # Standort-Intelligenz (Geo-Expand)
│   ├── output/
│   │   ├── __init__.py
│   │   ├── markdown.py       # Markdown-Dossier generieren
│   │   └── pdf.py            # PDF-Export
│   └── models.py             # Pydantic Data Models
├── tests/
├── requirements.txt
├── README.md
└── setup.sh
```

---

## Setup
```bash
cd /home/ubuntu/projects/person-intel-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Playwright Chromium wird von run.py automatisch installiert,
# falls es noch fehlt. Manuell nur bei Bedarf:
# python -m playwright install chromium

# Tools installieren
pip install sherlock-project maigret theHarvester

# Optional: dlib backend fuer FaceEngine
# macOS: zuerst cmake installieren, z.B. `brew install cmake`
pip install face_recognition
```

## Usage (geplant)
```bash
# Einfache Suche
python -m app.main search "Max Mustermann"

# Mit Filtern
python -m app.main search "Max Mustermann" --location "NRW" --country "DE" --language "de"

# Mit Bild
python -m app.main search "Max Mustermann" --photo photo.jpg

# Nur bestimmte Scanner
python -m app.main search "Max Mustermann" --scanners social,web,email

# Output als PDF
python -m app.main search "Max Mustermann" --format pdf
```

---

## Status
✅ **Production Ready** — Feature-rich webapp, 208 tests passing.

### Web App Features (v0.5.0)
- 🔍 Search with photo upload + auto-complete suggestions
- 📊 Dashboard (6 stat cards, search activity chart, top platforms chart, recent dossier)
- 📋 Results: Social, Web, Images, Professional, Emails, Timeline, Activity
- ⚖️ Compare View (side-by-side dossier comparison + diff)
- 📁 Bulk Upload (CSV, real-time progress tracking)
- 📥 Exports: JSON, HTML Report, Markdown, PDF, CSV History, ZIP (all-in-one)
- 🔍 Filters: Confidence, Platform, Verified, Bio, Min Similarity, Dossier Search
- 📈 Analytics Modal (confidence score, platform breakdown)
- 🛡️ Risk Score (exposure assessment, 0-100, recommendations)
- 🌓 Dark/Light Theme with persistence
- ⚡ 11 Scanner Status cards with rate stats
- 🕐 Dossier Timeline (chronological view)
- 📥 Search History export (JSON/CSV with notes/tags)
- 🏷️ Tags + Pins + Notes organization
- 🔍 Global Dossier Search Widget
- 🔑 Login Manager (LinkedIn, Xing, Instagram sessions)
- 🔗 Share Links (read-only dossier sharing)
- ⌨️ Keyboard Shortcuts (/ or Ctrl+K, Ctrl+E, Ctrl+D, ?, Esc)
- 📋 Search Templates (7 predefined profiles)
- 🖥️ VNC Server (remote desktop for VPS)

### Performance
- 🚀 Async Scanner Execution (11 scanners in parallel via asyncio.gather)
- 💾 Scanner Caching (30min memory + disk cache)
- ⏱️ Rate Limiting (domain-based delays)
- 📊 API Rate Tracking (requests, errors, duration per scanner)

### Analysis Modules
- Smart Deduplication (URL/Username/Email)
- Location Intelligence (50+ cities: DE/AT/CH/US/UK)
- Confidence Scoring
- Risk Score (exposure assessment)
- Dossier Diff (compare two scans)

### API Endpoints (v2)
- `GET /api/v2/dossier/{id}/filter` — Filter results
- `GET /api/v2/dossier/{id}/analytics` — Statistics
- `GET /api/v2/dossier/{id}/platforms` — Platform list
- `GET /api/v2/search/compare?id1=X&id2=Y` — Compare dossiers
- `GET /api/v2/platforms/summary` — Global stats
- `POST /api/bulk/upload` — CSV bulk search
- `GET /api/timeline/{id}` — Chronological timeline
- `GET /api/scanners/status` — Scanner health check
- `GET /api/suggest/names?q=` — Name auto-complete
- `GET /api/suggest/locations?q=` — Location suggestions
- `GET /api/dossiers/export-all` — Export all dossiers (JSON/CSV/Markdown)
- `GET /api/dossier/{id}/risk` — Risk score
- `GET /api/dossier/{id}/diff/{other_id}` — Dossier diff
- `GET /api/dossier/{id}/share` — Create share link
- `GET /api/dossier/{id}/download?fmt=html|markdown|pdf` — Download dossier
- `GET /api/dossier/{id}/download/zip` — Download as ZIP
- `GET /api/dossiers/search?q=` — Full-text search across dossiers
- `GET /api/dossiers/stats` — Dossier statistics
- `GET /api/sessions` — Login session status
- `POST /api/sessions/{platform}/cookies` — Upload cookies
- `GET /api/templates` — Search templates
- `GET /api/rate-stats` — API rate statistics
- `POST /api/cache/clear` — Clear scanner cache
- `GET /api/activity` — Activity log
- `POST /api/history/pin/{id}` — Pin search
- `POST /api/history/tags/{id}` — Add/remove tag
- `POST /api/history/notes/{id}` — Add notes (source reliability + match quality)

### Implemented
- [x] Pydantic Models (Person, SearchResult, SocialProfile, ImageMatch, Dossier)
- [x] CLI mit Typer (search, login, serve, face_analyze, face_compare, face_batch, ai_analyze)
- [x] BaseScanner Interface + Name/Location Varianten
- [x] Scanner: Web Search (DuckDuckGo + Bing + Yandex)
- [x] Scanner: Social Media (Username-Generierung + Platform-Checks)
- [x] Scanner: Email (theHarvester Wrapper)
- [x] Scanner: Professional (LinkedIn/Xing mit Cookie-Auth)
- [x] Scanner: Advanced Image + Reverse Image Search
- [x] Scanner: Deep Social + Professional Intel + Public Records
- [x] Scanner: Data Enrichment
- [x] Face Engine (DeepFace/ArcFace + dlib + Quality Assessment)
- [x] AI Analyzer (LLM Integration: Summary, Connections, Narrative, Anomalies)
- [x] Report Generator (PDF, HTML, JSON, CSV, Markdown)
- [x] Web App (FastAPI + SSE Progress + Search History)
- [x] Caching + Rate Limiting + Retry Logic
- [x] Smart Deduplication (URL/Username/Email across scanners)
- [x] Location Intelligence (50+ cities: DE/AT/CH/US/UK geo-expansion)
- [x] Confidence Scoring (source reliability + name match + completeness)
- [x] 100 Tests (Models, Scanners, Cache, Dedup, Location, Scoring, Face Engine)

## Nächste Schritte
1. `requirements.txt` erstellen
2. Pydantic Models implementieren
3. BaseScanner Interface + erster funktionierender Scanner (Web Search)

## Risiken / Ethik
- **Nur öffentliche Daten** verwenden
- **Rate Limiting** respektieren (keine Platform blocken)
- **Keine Privatsphäre-Verletzung** — nur was jeder finden könnte
- **Legal Review** vor Veröffentlichung nötig
- Bilder nur mit **Einwilligung** oder für legitime Zwecke
