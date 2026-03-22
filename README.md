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
- **Bildanalyse:** face_recognition (dlib), OpenCV
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

# Tools installieren
pip install sherlock-project maigret theHarvester face_recognition
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
🚧 **Initialisierung** — Projektstruktur erstellt, nächste Schritte:

1. [ ] Pydantic Models definieren (Person, SearchResult, Profile)
2. [ ] CLI mit Typer/Click aufsetzen
3. [ ] Scanner-Interface definieren (BaseScanner)
4. [ ] Erster Scanner: Web Search (Google)
5. [ ] Zweiter Scanner: Social Media (Sherlock Wrapper)
6. [ ] Location Intelligence implementieren
7. [ ] Image Similarity Pipeline
8. [ ] Markdown-Dossier Generator
9. [ ] PDF Export
10. [ ] OpenClaw Skill Integration

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
