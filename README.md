# Person Intel Agent

**Version 0.6.0 · status: MVP / beta — not production-hardened.**

Image-first OSINT **candidate discovery and verification**. Upload one or more reference photos (optionally with
identity hints). The app then:

1. finds pages where the photo appears,
2. discovers candidate profiles,
3. compares the faces on those candidates with the target face locally,
4. ranks candidates and shows exactly **why** each one was ranked.

> For self-search, consented searches, public-person research or otherwise authorised investigations only.
> Every result is a *candidate match* that must be verified independently. Evidence levels and face-similarity bands
> are descriptive — **they are not calibrated probabilities**.

## Quick start

```bash
python run.py --install          # creates .venv and installs requirements.txt
cp .env.example .env             # configure providers (see below)
python run.py                    # http://127.0.0.1:8000
```

CLI (same investigation engine as the web UI / REST API):

```bash
python -m app investigate -i photo.jpg                               # image only
python -m app investigate -i photo.jpg --name "Jane Doe" -l Vienna \
       --employer "Example GmbH" -x md -x pdf -o reports/            # photo + hints, with exports
python -m app face-compare a.jpg b.jpg                               # debug helper
python -m app purge                                                  # apply retention policy now
python -m app serve                                                  # production-style server (no reload)
```

Development server with auto-reload: `python run.py --dev`. It is refused when `APP_ENV=production`.

## Required external configuration

| What | Env vars | Notes |
|---|---|---|
| Google Cloud Vision Web Detection (primary reverse-image provider) | `GOOGLE_VISION_ENABLED=true` + `GOOGLE_VISION_API_KEY` **or** `GOOGLE_APPLICATION_CREDENTIALS` | Service accounts need `pip install google-auth`. |
| TinEye API (optional, exact/near-duplicate copies) | `TINEYE_ENABLED=true`, `TINEYE_API_KEY`, `TINEYE_API_URL` | Not facial recognition. |
| Web search | `SEARXNG_URL` (self-hosted SearXNG with JSON output) and/or `DDGS_ENABLED` | Used only when hints are supplied (or for Google web-entity hints). |
| Face matching | `deepface` + `tf-keras` (in `requirements.txt`, Python < 3.13) | ArcFace weights (~137 MB) download on first use. Without DeepFace the app runs with detection only. |
| Optional | `GITHUB_TOKEN`, `HIBP_API_KEY`, `APP_API_TOKEN`, `SESSION_ENCRYPTION_KEY` | See `.env.example`. |

No provider is required to start the app. Each unconfigured provider is reported as `NOT_CONFIGURED`, and the rest
of the pipeline keeps working.

## How an investigation works

```
reference image(s) ─ validate (content-sniffed JPEG/PNG/WebP, size, pixel bomb, EXIF orientation, metadata stripped)
                   ─ SHA-256 + pHash · face detection · target-face selection · quality GOOD/USABLE/POOR · ArcFace embedding
        │
        ├─ reverse image discovery (Google Vision, TinEye — concurrent, cached)  ──┐
        └─ parameter-assisted discovery (bounded query plan, GitHub/Wikidata/     ─┤
           Gravatar profile APIs, known URLs)                                      │
                                                                                   ▼
   candidate pages (dedup by canonical URL) → SSRF-safe fetch & parse (OpenGraph, JSON-LD Person, avatars, links)
   → candidate images (prioritised, ≤ N per page, fetched once, dedup by SHA-256/pHash)
   → faces + embeddings (cached by content hash) → compare with target face(s) (every face, not only the largest)
   → text evidence vs. supplied hints → clustering → evidence fusion → ranking → leads → persisted result
```

`app/services/investigation_service.py` is the **only** pipeline. REST, SSE, CLI and UI all call it. They produce
the same result (and the same input fingerprint) for the same inputs and configuration; a test enforces this.

### Two separate image problems

* **Reverse image matching** (providers): where does this *photo* or an edited/cropped copy appear? An exact match
  proves that the photo occurs on the page. It does *not* prove that every name on that page is the pictured person.
* **Face identity similarity** (local): does a *different* photo show the same face? The model is ArcFace (DeepFace)
  using cosine distance. A copy of the reference photo is never counted as independent face evidence.

Face bands (cosine distance, configurable). They are heuristic descriptions, not probabilities:

| Band | Distance | |
|---|---|---|
| VERY_HIGH | ≤ 0.40 | `FACE_VERY_HIGH_THRESHOLD` |
| HIGH | ≤ 0.55 | `FACE_HIGH_THRESHOLD` |
| MEDIUM | ≤ 0.68 | `FACE_MATCH_THRESHOLD` (DeepFace's published ArcFace verification threshold) |
| LOW | ≤ 0.80 | `FACE_LOW_THRESHOLD` |
| NO_MATCH | > 0.80 | |

With three or more reference photos, the *second*-closest reference decides the band, so one lucky match cannot
dominate.

### Evidence and fusion

Evidence items are **observations**: `EXACT_IMAGE`, `PARTIAL_IMAGE`, `SIMILAR_IMAGE`, `FACE_SIMILARITY`,
`NAME_MATCH`, `USERNAME_MATCH`, `LOCATION_MATCH`, `EMAIL_MATCH`, `EMPLOYER_MATCH`, `EDUCATION_MATCH`,
`PROFESSION_MATCH`, `KNOWN_URL`, `CROSS_LINK`. Each one carries its source URL, provider and timestamp.

The **conclusion** (`VERY_STRONG`, `STRONG`, `MODERATE`, `WEAK`, `INSUFFICIENT`) is rule-based and counts
*independent* strong signal categories:

* **IMAGE**: the reference photo occurs on a source.
* **FACE**: HIGH or better similarity on a *different* photo.
* **IDENTIFIER**: an exact username, email or URL match.
* **CONTEXT**: a full name *plus* location, employer or education.
* **CROSS_LINK**: an explicit link between sources.

Two strong signals give STRONG; three or more give VERY_STRONG. A name alone, or face similarity alone, can never
reach STRONG. If faces were compared and clearly do not match, the level is capped at WEAK. Sub-scores
(`image_occurrence_strength`, `face_match_strength`, `identity_text_strength`, `cross_source_strength`,
`source_quality`) are used only for ranking.

**Clustering** merges pages on an explicit cross-link, a shared email or a linked personal domain. It also merges on
two or more moderate links (handle, avatar pHash, face, name, organisation, location), where at least one link is
non-textual, or on three moderate links. Pages whose faces contradict each other relative to the target are never
merged on moderate links.

**Leads** are identifiers and hypotheses to follow up, never discoveries:

* `OBSERVED_EMAIL`, `PROVIDED_EMAIL` and `LINKED_PERSONAL_DOMAIN` are collected automatically.
* `GENERATED_EMAIL_CANDIDATE` and `DOMAIN_EXISTS_UNVERIFIED` are generated only when explicitly enabled.
* SMTP probing is off by default.

## Architecture

```
app/
  api/            FastAPI app factory, security middleware, routes (investigations, images, candidates, exports, sessions, system)
  cli.py          Typer CLI (investigate, serve, purge, face-compare, import-cookies)
  container.py    composition root (dependency wiring; test injection point)
  config.py       typed settings (pydantic-settings, env / .env)
  domain/         image, evidence, identity, candidate, investigation models
  services/       investigation_service (orchestrator), investigation_runner (background + SSE bus),
                  reference_image, image_discovery, candidate_discovery, page_analysis, candidate_image,
                  face_matching, text_evidence, candidate_clustering, evidence_fusion, candidate_ranking,
                  lead, report, retention, session
  providers/      reverse_image/{google_vision,tineye}, web_search/{searxng,ddgs}, profiles/{github,wikidata,gravatar}
  vision/         image_validation, image_hash (pHash), image_quality, face_detector (Haar fallback), face_embedder (DeepFace)
  infrastructure/ persistence (repository interface + SQLite), cache (namespaced TTL store), http (SSRF-safe fetcher),
                  security (URL guard, redaction, session store, rate limit)
  utils/          canonicalisation (URLs, image URLs, domains, emails, usernames, names), platform knowledge
```

### REST API

```
POST   /api/investigations                        multipart: images[]?, name?, location?, country?, age_min?, age_max?,
                                                  usernames?, emails?, employer?, university?, profession?, known_urls?, start?
GET    /api/investigations[?q=&tag=]              history
GET    /api/investigations/{id}                   investigation + result
PATCH  /api/investigations/{id}                   hints / options / pinned
DELETE /api/investigations/{id}
POST   /api/investigations/{id}/reference-images  add photos
PUT    /api/investigations/{id}/reference-images/{rid}/face   select target face
POST   /api/investigations/{id}/run               202 — runs in the background
GET    /api/investigations/{id}/events            Server-Sent Events (replay + live)
GET    /api/investigations/{id}/candidates[/{cid}]
GET    /api/investigations/{id}/evidence[?type=]
GET    /api/investigations/{id}/export?format=json|md|html|csv|pdf|zip
POST   /api/investigations/{id}/notes · /tags     (+ DELETE)
GET    /api/config · /api/health
```

Interactive docs are available at `/docs` (disabled when `APP_ENV=production`).

## Security & privacy

* **Uploads**: content-sniffed JPEG, PNG and WebP only, with size and pixel limits. EXIF orientation is applied and
  all metadata (including GPS) is stripped. Files get random names and `0600` permissions, and are never read
  outside `data/uploads`.
* **SSRF**: every remote page and image goes through one fetcher. It allows http(s) only, rejects credentials in
  URLs, internal hostnames, and private, loopback, link-local, CGNAT and metadata addresses. Redirects are
  re-validated on every hop. Byte limits are enforced while streaming, and MIME types are allow-listed. In direct
  mode the TCP connection goes to the validated IP, which defeats DNS rebinding. When `HTTP(S)_PROXY` is used, the
  check is a pre-flight DNS validation only.
* **Web app**:
  * strict CSP (no inline scripts), `X-Frame-Options: DENY`, `nosniff`, no-referrer;
  * all remote text is rendered with `textContent`, and HTML exports are escaped;
  * CSV exports neutralise formula injection;
  * body-size and rate limits;
  * generic 500 errors with a request id;
  * optional `APP_API_TOKEN`.
* **Sessions** (LinkedIn/Xing cookies) are disabled by default (`SESSION_MANAGEMENT_ENABLED`). When enabled:
  * they are held in memory; persistence is opt-in and Fernet-encrypted when `SESSION_ENCRYPTION_KEY` is set;
  * cookie values are never returned by any API or export;
  * the endpoints are localhost-only unless an API token protects the API.
* **Logs** redact keys, tokens and cookies.
* **Retention**: reference images and reference embeddings are deleted after 24 h by default, and candidate face
  crops after 30 days. Embedding caches expire. No permanent face database is built, and demographic inference
  (age/gender/emotion) is not used.
* **Production**: use `APP_ENV=production` with `python -m app serve` (no reload; docs disabled), put it behind a TLS
  reverse proxy, and set `APP_API_TOKEN`.

## Persistence

SQLite (`data/person_intel.db`) stores:

* investigations, hints and options;
* reference-image metadata;
* candidates and evidence (normalised tables plus the full result);
* provider runs;
* progress events;
* notes and tags.

Investigations survive restarts, and runs that were in progress are marked `INTERRUPTED`. PostgreSQL can be added by
implementing `InvestigationRepository`. Separate TTL caches (`data/cache.db`) cover web search, reverse image, page
parsing, profile lookups and face embeddings. Cache keys hash every input that affects the value, plus the pipeline
version.

## Tests

```bash
pip install -r requirements.txt
python -m pytest            # 111 tests, offline (mocked providers, synthetic imagery, fake face backend)
ruff check app tests && mypy app
```

The end-to-end scenarios cover:

* exact image;
* cropped image;
* a different photo of the same identity plus text hints;
* a wrong person with the same name;
* a visually similar wrong person;
* no match;
* provider failure isolation;
* clustering;
* REST ↔ CLI ↔ service equivalence;
* restart persistence;
* retention.

## Known limitations

* The face bands are uncalibrated heuristics. No benchmark calibration has been done.
* The age range hint is stored but not used for matching. Demographic inference is intentionally disabled.
* Many sites (LinkedIn, Instagram, Facebook) block anonymous fetching, so those pages often contribute only
  search-snippet or provider data.
* Only SQLite is implemented. The runner is in-process, so there is no distributed queue.
* The TinEye response mapping follows TinEye's documented fields, but it was validated against fixtures, not the live
  API.
