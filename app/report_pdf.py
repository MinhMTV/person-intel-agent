"""PDF report generator for dossiers."""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from fpdf import FPDF


class DossierPDF(FPDF):
    """Custom PDF with header/footer."""

    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.set_auto_page_break(auto=True, margin=15)

    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(50, 50, 50)
        self.cell(0, 10, f"Intel Dossier: {self.name}", new_x="LMARGIN", new_y="NEXT", align="C")
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 5, f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}", new_x="LMARGIN", new_y="NEXT", align="C")
        self.ln(3)
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Person Intel Agent | Page {self.page_no()}/{{nb}}", align="C")

    def section_title(self, title: str):
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(30, 100, 200)
        self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(30, 100, 200)
        self.line(10, self.get_y(), 80, self.get_y())
        self.ln(3)

    def body_text(self, text: str):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(50, 50, 50)
        self.multi_cell(0, 5, text)
        self.ln(2)

    def result_row(self, platform: str, url: str, confidence: str = ""):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(30, 30, 30)
        self.cell(45, 5, platform[:25])
        self.set_font("Helvetica", "", 8)
        self.set_text_color(0, 80, 180)
        url_display = url[:70] + "..." if len(url) > 70 else url
        self.cell(120, 5, url_display)
        if confidence:
            self.set_font("Helvetica", "I", 8)
            color = (0, 150, 0) if confidence == "high" else (200, 150, 0) if confidence == "medium" else (150, 150, 150)
            self.set_text_color(*color)
            self.cell(25, 5, confidence, new_x="LMARGIN", new_y="NEXT", align="R")
        else:
            self.ln(5)

    def stat_box(self, label: str, value: str):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(80, 80, 80)
        self.cell(45, 6, label)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        self.cell(30, 6, value, new_x="LMARGIN", new_y="NEXT")


def generate_pdf(dossier: Any) -> bytes:
    """Generate a PDF report from a dossier object."""
    pdf = DossierPDF(dossier.query.full_name)
    pdf.alias_nb_pages()
    pdf.add_page()

    # Summary stats
    pdf.section_title("Summary")
    pdf.stat_box("Confidence Score", f"{dossier.confidence_score:.1%}")
    pdf.stat_box("Scanners Used", ", ".join(dossier.scanners_used))
    pdf.stat_box("Social Profiles", str(len(dossier.social_profiles)))
    pdf.stat_box("Web Results", str(len(dossier.web_results)))
    pdf.stat_box("Image Matches", str(len(dossier.image_matches)))
    pdf.stat_box("Email Addresses", str(len(dossier.email_addresses)))
    pdf.stat_box("Professional", str(len(dossier.professional)))
    pdf.ln(5)

    # Social profiles
    if dossier.social_profiles:
        pdf.section_title("Social Profiles")
        for sp in dossier.social_profiles:
            pdf.result_row(sp.platform, sp.url, sp.confidence.value if hasattr(sp.confidence, "value") else str(sp.confidence))

    # Web results
    if dossier.web_results:
        pdf.section_title("Web Results")
        for wr in dossier.web_results:
            pdf.result_row(wr.source.value if hasattr(wr.source, "value") else str(wr.source), wr.url)
            if wr.snippet:
                pdf.set_font("Helvetica", "I", 7)
                pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 4, f"  {wr.snippet[:100]}", new_x="LMARGIN", new_y="NEXT")

    # Professional
    if dossier.professional:
        pdf.section_title("Professional")
        for pr in dossier.professional:
            pdf.result_row(pr.platform, pr.url)
            if pr.title:
                pdf.set_font("Helvetica", "I", 8)
                pdf.set_text_color(80, 80, 80)
                pdf.cell(0, 4, f"  {pr.title}", new_x="LMARGIN", new_y="NEXT")

    # Emails
    if dossier.email_addresses:
        pdf.section_title("Email Addresses")
        for em in dossier.email_addresses:
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(50, 50, 50)
            pdf.cell(0, 5, f"  {em}", new_x="LMARGIN", new_y="NEXT")

    # Image matches
    if dossier.image_matches:
        pdf.section_title("Image Matches")
        for im in dossier.image_matches:
            sim = im.similarity if hasattr(im, "similarity") else 0
            pdf.result_row(im.platform if hasattr(im, "platform") else "unknown", im.url if hasattr(im, "url") else "", f"{sim:.0%}")

    # Output
    return bytes(pdf.output())
