"""Report Generator — PDF, HTML, JSON, CSV export with timeline.

Features:
  - PDF report generation (WeasyPrint)
  - HTML report with timeline
  - JSON/CSV data export
  - Confidence scoring
  - Timeline visualization data
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.models import PersonDossier, PersonQuery, SearchResult, SocialProfile, ImageMatch, Source


class ReportGenerator:
    """Generate intelligence reports in multiple formats.

    Supports:
      - Markdown (.md)
      - HTML with timeline (.html)
      - PDF via WeasyPrint (.pdf)
      - JSON (.json)
      - CSV (.csv)
    """

    def __init__(self, output_dir: str = "output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)

    def generate(self, dossier: PersonDossier, formats: list[str] = None) -> dict[str, Path]:
        """Generate reports in specified formats.

        Returns dict mapping format to output file path.
        """
        if formats is None:
            formats = ["markdown", "json"]

        filename = dossier.query.full_name.replace(" ", "_").lower()
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M")
        base_name = f"{filename}_{timestamp}"

        outputs = {}

        for fmt in formats:
            try:
                if fmt == "markdown":
                    path = self._generate_markdown(dossier, base_name)
                    outputs["markdown"] = path
                elif fmt == "html":
                    path = self._generate_html(dossier, base_name)
                    outputs["html"] = path
                elif fmt == "pdf":
                    path = self._generate_pdf(dossier, base_name)
                    outputs["pdf"] = path
                elif fmt == "json":
                    path = self._generate_json(dossier, base_name)
                    outputs["json"] = path
                elif fmt == "csv":
                    path = self._generate_csv(dossier, base_name)
                    outputs["csv"] = path
            except Exception as e:
                print(f"  Report generation error ({fmt}): {e}")

        return outputs

    # =========================================================================
    # Markdown Report
    # =========================================================================

    def _generate_markdown(self, dossier: PersonDossier, base_name: str) -> Path:
        """Generate Markdown report."""
        lines = self._build_markdown_content(dossier)
        path = self.output_dir / f"{base_name}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _build_markdown_content(self, dossier: PersonDossier) -> list[str]:
        """Build Markdown report content."""
        lines = [
            f"# Person Intelligence Dossier",
            f"",
            f"**Name:** {dossier.query.full_name}",
            f"**Generated:** {dossier.generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
            f"**Sources Checked:** {dossier.total_sources_checked}",
            f"**Scanners Used:** {', '.join(dossier.scanners_used)}",
            f"",
        ]

        # Confidence score
        if dossier.confidence_score > 0:
            lines.append(f"**Overall Confidence:** {dossier.confidence_score:.0%}")
            lines.append("")

        # Social Profiles
        if dossier.social_profiles:
            lines.append("## 📱 Social Profiles")
            lines.append("")
            for p in dossier.social_profiles:
                verified = " ✅" if p.verified else ""
                bio_str = f" — {p.bio[:80]}" if p.bio else ""
                followers_str = f" ({p.followers:,} followers)" if p.followers else ""
                lines.append(f"- **{p.platform}**{verified}: [{p.username or p.url}]({p.url}){followers_str}{bio_str}")
            lines.append("")

        # Email Addresses
        if dossier.email_addresses:
            lines.append("## 📧 Email Addresses")
            lines.append("")
            for email in dossier.email_addresses:
                lines.append(f"- {email}")
            lines.append("")

        # Web Results
        if dossier.web_results:
            lines.append("## 🌐 Web Mentions")
            lines.append("")
            for r in dossier.web_results[:20]:
                source_tag = f"[{r.source.value}]" if r.source else ""
                snippet_str = f"\n  > {r.snippet[:100]}" if r.snippet else ""
                lines.append(f"- {source_tag} [{r.title[:60]}]({r.url}){snippet_str}")
            lines.append("")

        # Professional
        if dossier.professional:
            lines.append("## 💼 Professional")
            lines.append("")
            for r in dossier.professional[:10]:
                lines.append(f"- [{r.title[:60]}]({r.url})")
            lines.append("")

        # Academic
        if dossier.academic:
            lines.append("## 📚 Academic")
            lines.append("")
            for r in dossier.academic[:10]:
                lines.append(f"- [{r.title[:80]}]({r.url})")
            lines.append("")

        # Image Matches
        if dossier.image_matches:
            lines.append("## 🖼️ Image Matches")
            lines.append("")
            for m in dossier.image_matches[:10]:
                lines.append(f"- **{m.similarity_score:.0%}** match: [{m.source_url[:50]}]({m.source_url})")
                if m.context:
                    lines.append(f"  Context: {m.context}")
            lines.append("")

        # Timeline
        timeline = self._build_timeline(dossier)
        if timeline:
            lines.append("## 📅 Timeline")
            lines.append("")
            for entry in timeline:
                lines.append(f"- **{entry['date']}**: {entry['event']}")
            lines.append("")

        return lines

    def _build_timeline(self, dossier: PersonDossier) -> list[dict]:
        """Build timeline from dossier data."""
        events = []

        # Social profile creation dates
        for p in dossier.social_profiles:
            events.append({
                "date": p.found_at.strftime("%Y-%m-%d") if p.found_at else "Unknown",
                "event": f"Found on {p.platform}: {p.display_name or p.username}",
                "type": "social",
            })

        # Sort by date
        events.sort(key=lambda e: e["date"], reverse=True)
        return events[:20]

    # =========================================================================
    # HTML Report
    # =========================================================================

    def _generate_html(self, dossier: PersonDossier, base_name: str) -> Path:
        """Generate HTML report with styling."""
        md_lines = self._build_markdown_content(dossier)

        # Convert to basic HTML
        html_content = self._markdown_to_html("\n".join(md_lines))

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Intelligence Dossier: {dossier.query.full_name}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.6;
               color: #333; background: #fafafa; }}
        h1 {{ color: #1a1a2e; border-bottom: 3px solid #e94560; padding-bottom: 10px; }}
        h2 {{ color: #16213e; margin-top: 30px; }}
        a {{ color: #0f3460; }}
        ul {{ padding-left: 20px; }}
        li {{ margin-bottom: 8px; }}
        .meta {{ background: #f0f0f0; padding: 15px; border-radius: 8px; margin-bottom: 20px; }}
        .confidence {{ display: inline-block; padding: 3px 10px; border-radius: 4px;
                       font-weight: bold; }}
        .confidence.high {{ background: #d4edda; color: #155724; }}
        .confidence.medium {{ background: #fff3cd; color: #856404; }}
        .confidence.low {{ background: #f8d7da; color: #721c24; }}
        @media print {{ body {{ background: white; }} }}
    </style>
</head>
<body>
{html_content}
<footer style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #ddd; color: #666; font-size: 0.9em;">
    Generated by Person Intelligence Agent — {dossier.generated_at.strftime('%Y-%m-%d %H:%M UTC')}
</footer>
</body>
</html>"""

        path = self.output_dir / f"{base_name}.html"
        path.write_text(html, encoding="utf-8")
        return path

    def _markdown_to_html(self, md: str) -> str:
        """Simple Markdown to HTML conversion."""
        lines = md.split("\n")
        html_lines = []

        for line in lines:
            if line.startswith("# "):
                html_lines.append(f"<h1>{line[2:]}</h1>")
            elif line.startswith("## "):
                html_lines.append(f"<h2>{line[3:]}</h2>")
            elif line.startswith("- **"):
                # Bold list item
                html_lines.append(f"<li>{line[2:]}</li>")
            elif line.startswith("- "):
                html_lines.append(f"<li>{line[2:]}</li>")
            elif line.startswith("**") and line.endswith("**"):
                html_lines.append(f"<p><strong>{line[2:-2]}</strong></p>")
            elif line.strip():
                html_lines.append(f"<p>{line}</p>")

        return "\n".join(html_lines)

    # =========================================================================
    # PDF Report
    # =========================================================================

    def _generate_pdf(self, dossier: PersonDossier, base_name: str) -> Path:
        """Generate PDF report using WeasyPrint."""
        try:
            from weasyprint import HTML
        except ImportError:
            print("  WeasyPrint not available, falling back to HTML")
            return self._generate_html(dossier, base_name)

        html_path = self._generate_html(dossier, base_name)
        pdf_path = self.output_dir / f"{base_name}.pdf"

        HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        return pdf_path

    # =========================================================================
    # JSON Export
    # =========================================================================

    def _generate_json(self, dossier: PersonDossier, base_name: str) -> Path:
        """Generate JSON export."""
        path = self.output_dir / f"{base_name}.json"
        path.write_text(dossier.model_dump_json(indent=2), encoding="utf-8")
        return path

    # =========================================================================
    # CSV Export
    # =========================================================================

    def _generate_csv(self, dossier: PersonDossier, base_name: str) -> Path:
        """Generate CSV export (social profiles + web results)."""
        path = self.output_dir / f"{base_name}.csv"

        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow(["Category", "Platform/Source", "Title/Username", "URL", "Details", "Confidence"])

        # Social profiles
        for p in dossier.social_profiles:
            writer.writerow([
                "Social Profile",
                p.platform,
                p.display_name or p.username or "",
                p.url,
                p.bio or "",
                p.confidence.value,
            ])

        # Web results
        for r in dossier.web_results:
            writer.writerow([
                "Web Result",
                r.source.value if r.source else "",
                r.title,
                r.url,
                r.snippet or "",
                r.confidence.value,
            ])

        # Emails
        for email in dossier.email_addresses:
            writer.writerow(["Email", "", email, f"mailto:{email}", "", ""])

        # Image matches
        for m in dossier.image_matches:
            writer.writerow([
                "Image Match",
                "",
                f"{m.similarity_score:.0%} match",
                m.source_url,
                m.context or "",
                "",
            ])

        path.write_text(output.getvalue(), encoding="utf-8")
        return path
