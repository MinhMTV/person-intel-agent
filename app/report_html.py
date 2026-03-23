"""Enhanced Report Templates — rich HTML dossier with interactive elements."""

from __future__ import annotations

import html as html_module
from datetime import datetime
from pathlib import Path

from app.models import PersonDossier, Confidence


def generate_html_report(dossier: PersonDossier, dossier_id: str) -> str:
    """Generate a standalone HTML report with embedded styles and interactivity."""
    name = html_module.escape(dossier.query.full_name)
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    conf_pct = f"{(dossier.confidence_score or 0) * 100:.0f}%"

    # Count stats
    total = (
        len(dossier.social_profiles) + len(dossier.web_results)
        + len(dossier.image_matches) + len(dossier.email_addresses)
        + len(dossier.professional) + len(dossier.academic)
    )

    social_html = _render_social_section(dossier.social_profiles)
    web_html = _render_web_section(dossier.web_results)
    email_html = _render_email_section(dossier.email_addresses)
    professional_html = _render_professional_section(dossier.professional)
    image_html = _render_image_section(dossier.image_matches)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OSINT Dossier: {name}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f172a;color:#e2e8f0;line-height:1.6;padding:2rem}}
.container{{max-width:900px;margin:0 auto}}
header{{text-align:center;margin-bottom:2rem;padding-bottom:1.5rem;border-bottom:1px solid #334155}}
h1{{font-size:2rem;margin-bottom:.5rem}}
.subtitle{{color:#94a3b8;font-size:.9rem}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:1rem;margin:1.5rem 0}}
.stat-card{{background:#1e293b;border-radius:12px;padding:1rem;text-align:center}}
.stat-card .number{{font-size:1.5rem;font-weight:700;color:#60a5fa}}
.stat-card .label{{font-size:.75rem;color:#94a3b8;text-transform:uppercase}}
.stat-card.green .number{{color:#4ade80}}
.stat-card.amber .number{{color:#fbbf24}}
.stat-card.purple .number{{color:#a78bfa}}
section{{margin-bottom:2rem}}
section h2{{font-size:1.1rem;margin-bottom:1rem;padding-bottom:.5rem;border-bottom:1px solid #334155;display:flex;align-items:center;gap:.5rem}}
.card{{background:#1e293b;border-radius:12px;padding:1rem;margin-bottom:.75rem;display:flex;align-items:center;gap:1rem}}
.card:hover{{background:#253049}}
.card .icon{{width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:1.2rem;flex-shrink:0}}
.card .info{{flex:1;min-width:0}}
.card .info a{{color:#60a5fa;text-decoration:none;font-weight:500}}
.card .info a:hover{{text-decoration:underline}}
.card .info .meta{{font-size:.8rem;color:#94a3b8;margin-top:.25rem}}
.card .badge{{padding:.2rem .6rem;border-radius:9999px;font-size:.7rem;font-weight:600;text-transform:uppercase}}
.badge.high{{background:rgba(74,222,128,.2);color:#4ade80}}
.badge.medium{{background:rgba(251,191,36,.2);color:#fbbf24}}
.badge.low{{background:rgba(100,116,139,.3);color:#94a3b8}}
.platform-icon{{background:#334155;color:#e2e8f0}}
.email-icon{{background:#78350f;color:#fbbf24}}
.web-icon{{background:#064e3b;color:#4ade80}}
.prof-icon{{background:#164e63;color:#22d3ee}}
footer{{text-align:center;padding-top:1.5rem;border-top:1px solid #334155;color:#64748b;font-size:.75rem}}
@media print{{body{{background:#fff;color:#1e293b}}.card{{border:1px solid #e2e8f0}}}}
</style>
</head>
<body>
<div class="container">
<header>
<h1>🔍 OSINT Dossier</h1>
<div class="subtitle">{name} — Generated {ts}</div>
</header>

<div class="stats">
<div class="stat-card green"><div class="number">{conf_pct}</div><div class="label">Confidence</div></div>
<div class="stat-card"><div class="number">{total}</div><div class="label">Total Results</div></div>
<div class="stat-card"><div class="number">{len(dossier.social_profiles)}</div><div class="label">Social</div></div>
<div class="stat-card"><div class="number">{len(dossier.web_results)}</div><div class="label">Web</div></div>
<div class="stat-card amber"><div class="number">{len(dossier.email_addresses)}</div><div class="label">Emails</div></div>
<div class="stat-card purple"><div class="number">{len(dossier.image_matches)}</div><div class="label">Images</div></div>
</div>

{social_html}
{web_html}
{professional_html}
{email_html}
{image_html}

<footer>
Person Intel Agent — OSINT Dossier — Generated {ts}<br>
For authorized research only. Verify all findings independently.
</footer>
</div>
</body>
</html>"""


def _render_social_section(profiles) -> str:
    if not profiles:
        return ""
    cards = ""
    for p in profiles:
        conf = p.confidence.value if hasattr(p.confidence, 'value') else str(p.confidence)
        verified = " ✓" if p.verified else ""
        bio = html_module.escape(p.bio[:80] + "...") if p.bio and len(p.bio) > 80 else html_module.escape(p.bio or "")
        name_display = html_module.escape(p.display_name or p.username or p.platform)
        cards += f"""<div class="card">
<div class="icon platform-icon">{html_module.escape(p.platform[0].upper())}</div>
<div class="info">
<a href="{html_module.escape(p.url)}" target="_blank">{name_display}{verified}</a>
<div class="meta">{html_module.escape(p.platform)} · {bio}</div>
</div>
<span class="badge {conf}">{conf}</span>
</div>\n"""
    return f"<section><h2>👤 Social Profiles ({len(profiles)})</h2>\n{cards}</section>"


def _render_web_section(results) -> str:
    if not results:
        return ""
    cards = ""
    for r in results[:15]:
        conf = r.confidence.value if hasattr(r.confidence, 'value') else str(r.confidence)
        snippet = html_module.escape((r.snippet or "")[:100])
        cards += f"""<div class="card">
<div class="icon web-icon">🌐</div>
<div class="info">
<a href="{html_module.escape(r.url)}" target="_blank">{html_module.escape(r.title)}</a>
<div class="meta">{snippet}</div>
</div>
<span class="badge {conf}">{conf}</span>
</div>\n"""
    return f"<section><h2>🌐 Web Results ({len(results)})</h2>\n{cards}</section>"


def _render_email_section(emails) -> str:
    if not emails:
        return ""
    cards = ""
    for e in emails:
        cards += f"""<div class="card">
<div class="icon email-icon">📧</div>
<div class="info"><span style="font-family:monospace">{html_module.escape(e)}</span></div>
</div>\n"""
    return f"<section><h2>📧 Email Addresses ({len(emails)})</h2>\n{cards}</section>"


def _render_professional_section(results) -> str:
    if not results:
        return ""
    cards = ""
    for r in results:
        cards += f"""<div class="card">
<div class="icon prof-icon">💼</div>
<div class="info">
<a href="{html_module.escape(r.url)}" target="_blank">{html_module.escape(r.title)}</a>
<div class="meta">{html_module.escape(r.snippet or '')[:80]}</div>
</div>
</div>\n"""
    return f"<section><h2>💼 Professional ({len(results)})</h2>\n{cards}</section>"


def _render_image_section(matches) -> str:
    if not matches:
        return ""
    cards = ""
    for m in matches[:10]:
        pct = f"{m.similarity_score * 100:.0f}%"
        color = "green" if m.similarity_score > 0.8 else "amber" if m.similarity_score > 0.6 else ""
        cards += f"""<div class="card">
<div class="icon" style="background:#4c1d95;color:#a78bfa">🖼️</div>
<div class="info">
<a href="{html_module.escape(m.source_url)}" target="_blank">{html_module.escape(m.source_url[:50])}…</a>
<div class="meta">{html_module.escape(m.context or '')[:60]}</div>
</div>
<span class="badge {'high' if m.similarity_score > 0.8 else 'medium' if m.similarity_score > 0.6 else 'low'}">{pct}</span>
</div>\n"""
    return f"<section><h2>🖼️ Image Matches ({len(matches)})</h2>\n{cards}</section>"
