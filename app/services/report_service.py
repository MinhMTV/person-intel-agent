"""Exports (JSON / Markdown / HTML / CSV / PDF / ZIP).

Every format is rendered from the same persisted ``Investigation`` +
``InvestigationResult``. Exports never contain face embeddings, cookies or
face-crop thumbnails.
"""

from __future__ import annotations

import contextlib
import csv
import html
import io
import json
import re
import zipfile
from typing import Any

from app import __version__
from app.domain.candidate import CandidateIdentity
from app.domain.investigation import Investigation, InvestigationResult

DISCLAIMER = (
    "Findings are CANDIDATE matches produced by automated tools. Evidence levels are descriptive and are NOT "
    "calibrated probabilities. Face similarity is not proof of identity. Verify every finding independently "
    "and use this tool only for self-search, consented or otherwise authorised research."
)
_STRIP_KEYS = {"thumbnail", "candidate_thumbnail", "embedding", "stored_path", "text_excerpt", "outbound_links"}


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in _STRIP_KEYS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _filename(inv: Investigation) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", inv.title.lower()).strip("_")[:40] or "investigation"
    return f"{base}_{inv.id[:8]}"


def _hints_rows(result: InvestigationResult) -> list[tuple[str, str]]:
    h = result.hints
    rows = [
        ("Name", h.name),
        ("Location", h.location),
        ("Country", h.country),
        ("Age range", f"{h.age_min or '?'}–{h.age_max or '?'}" if (h.age_min or h.age_max) else None),
        ("Usernames", ", ".join(h.usernames)),
        ("Emails", ", ".join(h.emails)),
        ("Employer", h.employer),
        ("University", h.university),
        ("Profession", h.profession),
        ("Known URLs", ", ".join(h.known_urls)),
    ]
    return [(k, str(v)) for k, v in rows if v]


def _summary_rows(c: CandidateIdentity) -> list[tuple[str, str]]:
    a = c.assessment
    return [
        ("Overall evidence", a.level.value),
        ("Face similarity", a.face_band.value if a.face_band else "not compared"),
        ("Image occurrence", a.image_occurrence.value if a.image_occurrence else "none"),
        ("Name match", a.name_match.value),
        ("Location match", a.location_match.value),
        ("Username match", a.username_match.value),
        ("Email match", a.email_match.value),
        ("Employer match", a.employer_match.value),
        ("Education match", a.education_match.value),
        ("Independent sources", str(a.independent_sources)),
    ]


class ReportService:
    # ------------------------------------------------------------------ JSON
    def to_dict(self, inv: Investigation) -> dict[str, Any]:
        result = inv.result
        return {
            "generator": f"person-intel-agent {__version__}",
            "disclaimer": DISCLAIMER,
            "investigation": {
                "id": inv.id,
                "title": inv.title,
                "status": inv.status.value,
                "created_at": inv.created_at.isoformat(),
                "tags": inv.tags,
                "pinned": inv.pinned,
                "notes": [{"text": n.text, "created_at": n.created_at.isoformat()} for n in inv.notes],
            },
            "result": _strip(result.model_dump(mode="json")) if result else None,
        }

    def to_json(self, inv: Investigation) -> bytes:
        return json.dumps(self.to_dict(inv), indent=2, ensure_ascii=False).encode("utf-8")

    # -------------------------------------------------------------- Markdown
    def to_markdown(self, inv: Investigation) -> str:
        r = inv.result
        lines = [f"# Investigation: {inv.title}", "", f"> {DISCLAIMER}", ""]
        lines += [
            f"- **ID:** {inv.id}",
            f"- **Status:** {inv.status.value}",
            f"- **Created:** {inv.created_at:%Y-%m-%d %H:%M} UTC",
        ]
        if inv.tags:
            lines.append(f"- **Tags:** {', '.join(inv.tags)}")
        if r is None:
            lines += ["", "_This investigation has not produced results yet._"]
            return "\n".join(lines)
        lines += [
            f"- **Pipeline version:** {r.pipeline_version} · fingerprint `{r.fingerprint[:16]}`",
            f"- **Face model:** {r.face_model or 'unavailable'}",
            "",
            "## Conclusion",
            "",
            r.conclusion,
            "",
        ]
        hints = _hints_rows(r)
        lines += ["## Inputs", ""]
        lines += [f"- {k}: {v}" for k, v in hints] or ["- No identity hints supplied (image-only search)."]
        for ref in r.reference_images:
            lines.append(
                f"- Reference image {ref.filename or ref.id}: {ref.width}×{ref.height}, quality "
                f"{ref.quality.label.value}, {len(ref.faces)} face(s), sha256 `{ref.sha256[:16]}…`"
            )
        lines.append("")
        lines += ["## Candidates", ""]
        if not r.candidates:
            lines.append("No candidates.")
        for c in r.candidates:
            lines += [f"### {c.rank}. {c.display_name} — {c.assessment.level.value}", ""]
            lines += ["| Signal | Value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in _summary_rows(c)]
            if c.best_face:
                lines.append(
                    f"\nBest face comparison: {c.best_face.model}, cosine similarity "
                    f"{c.best_face.cosine_similarity:.3f}, distance {c.best_face.distance:.3f} "
                    f"({c.best_face.match_band.value})."
                )
            lines += ["", "**Why:**"] + [f"- {x}" for x in c.assessment.reasons]
            if c.assessment.caveats:
                lines += ["", "**Caveats:**"] + [f"- {x}" for x in c.assessment.caveats]
            lines += ["", "**Sources:**"] + [f"- {u}" for u in c.urls]
            if c.cluster_reasons:
                lines += ["", "**Why these sources were grouped:**"] + [f"- {x}" for x in c.cluster_reasons]
            lines += ["", "**Evidence (observations):**"]
            for ev in c.evidence:
                lines.append(
                    f"- [{ev.type.value} · {ev.strength.value}] {ev.observation}"
                    + (f" — {ev.source_url}" if ev.source_url else "")
                )
            lines.append("")
        if r.reverse_image_results:
            lines += ["## Reverse image results", ""]
            for res in r.reverse_image_results[:50]:
                lines.append(
                    f"- {res.provider} · {res.match_type.value} · page: {res.page_url or '—'} · image: {res.image_url or '—'}"
                )
            lines.append("")
        if r.web_entities or r.best_guess_labels:
            lines += ["## Web entities (provider labels — not identity evidence)", ""]
            lines += [f"- Best guess: {x}" for x in r.best_guess_labels]
            lines += [
                f"- {e.description} ({e.score:.2f})" if e.score is not None else f"- {e.description}"
                for e in r.web_entities
            ]
            lines.append("")
        if r.leads:
            lines += ["## Leads (hypotheses and identifiers to follow up)", ""]
            lines += [
                f"- {lead.classification}: {lead.value}" + (f" — {lead.note}" if lead.note else "") for lead in r.leads
            ]
            lines.append("")
        lines += [
            "## Provider runs",
            "",
            "| Provider | Stage | Outcome | Results | ms | Cache |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {p.provider} | {p.stage} | {p.outcome.value} | {p.result_count} | {p.duration_ms} | {'hit' if p.cache_hit else ''} |"
            for p in r.provider_runs
        ]
        s = r.stats
        lines += [
            "",
            "## Statistics",
            "",
            f"- Pages discovered: {s.pages_discovered} (fetched {s.pages_fetched})",
            f"- Images downloaded: {s.images_downloaded} (deduplicated {s.images_deduplicated})",
            f"- Faces detected: {s.faces_detected}; comparisons: {s.face_comparisons}",
            f"- Candidates: {s.candidate_count}; duration: {s.duration_ms} ms; cache hits/misses: {s.cache_hits}/{s.cache_misses}",
        ]
        if r.warnings:
            lines += ["", "## Warnings", ""] + [f"- {w}" for w in r.warnings]
        if inv.notes:
            lines += ["", "## Notes", ""] + [f"- ({n.created_at:%Y-%m-%d}) {n.text}" for n in inv.notes]
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------ HTML
    def to_html(self, inv: Investigation) -> str:
        e = html.escape
        r = inv.result

        def link(url: str | None) -> str:
            if not url or not url.startswith(("http://", "https://")):
                return e(url or "—")
            return f'<a href="{e(url)}" rel="noopener noreferrer nofollow">{e(url)}</a>'

        parts = [
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">",
            f"<title>{e(inv.title)} — investigation report</title>",
            "<style>body{font:14px/1.5 system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#1f2933}"
            "h1,h2,h3{line-height:1.2}table{border-collapse:collapse;margin:.5rem 0}td,th{border:1px solid #d9e2ec;padding:4px 8px;text-align:left}"
            ".note{background:#fff8e1;border-left:4px solid #f0b429;padding:.75rem 1rem}.lvl{font-weight:700}"
            "li{margin:.2rem 0;word-break:break-word}</style></head><body>",
            f"<h1>Investigation: {e(inv.title)}</h1><p class='note'>{e(DISCLAIMER)}</p>",
            f"<p>ID {e(inv.id)} · status {e(inv.status.value)} · created {e(inv.created_at.strftime('%Y-%m-%d %H:%M'))} UTC</p>",
        ]
        if r is None:
            parts.append("<p>No results yet.</p></body></html>")
            return "".join(parts)
        parts.append(f"<h2>Conclusion</h2><p>{e(r.conclusion)}</p><h2>Inputs</h2><ul>")
        hints = _hints_rows(r)
        parts += [f"<li>{e(k)}: {e(v)}</li>" for k, v in hints] or ["<li>No identity hints (image-only search).</li>"]
        parts += [
            f"<li>Reference image {e(ref.filename or ref.id)}: {ref.width}×{ref.height}, quality "
            f"{e(ref.quality.label.value)}, {len(ref.faces)} face(s)</li>"
            for ref in r.reference_images
        ]
        parts.append("</ul><h2>Candidates</h2>")
        for c in r.candidates:
            parts.append(
                f"<h3>{c.rank}. {e(c.display_name)} — <span class='lvl'>{e(c.assessment.level.value)}</span></h3><table>"
            )
            parts += [f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in _summary_rows(c)]
            parts.append("</table><p><b>Why</b></p><ul>")
            parts += [f"<li>{e(x)}</li>" for x in c.assessment.reasons]
            parts.append("</ul>")
            if c.assessment.caveats:
                parts.append(
                    "<p><b>Caveats</b></p><ul>" + "".join(f"<li>{e(x)}</li>" for x in c.assessment.caveats) + "</ul>"
                )
            parts.append("<p><b>Sources</b></p><ul>" + "".join(f"<li>{link(u)}</li>" for u in c.urls) + "</ul>")
            parts.append("<p><b>Evidence (observations)</b></p><ul>")
            parts += [
                f"<li>[{e(ev.type.value)} · {e(ev.strength.value)}] {e(ev.observation)} {link(ev.source_url)}</li>"
                for ev in c.evidence
            ]
            parts.append("</ul>")
        if r.leads:
            parts.append(
                "<h2>Leads</h2><ul>"
                + "".join(
                    f"<li>{e(lead.classification)}: {e(lead.value)} {e(lead.note or '')}</li>" for lead in r.leads
                )
                + "</ul>"
            )
        parts.append(
            "<h2>Provider runs</h2><table><tr><th>Provider</th><th>Stage</th><th>Outcome</th><th>Results</th><th>ms</th></tr>"
        )
        parts += [
            f"<tr><td>{e(p.provider)}</td><td>{e(p.stage)}</td><td>{e(p.outcome.value)}</td><td>{p.result_count}</td>"
            f"<td>{p.duration_ms}</td></tr>"
            for p in r.provider_runs
        ]
        parts.append("</table>")
        if r.warnings:
            parts.append("<h2>Warnings</h2><ul>" + "".join(f"<li>{e(w)}</li>" for w in r.warnings) + "</ul>")
        if inv.notes:
            parts.append("<h2>Notes</h2><ul>" + "".join(f"<li>{e(n.text)}</li>" for n in inv.notes) + "</ul>")
        parts.append("</body></html>")
        return "".join(parts)

    # ------------------------------------------------------------------- CSV
    def to_csv(self, inv: Investigation) -> str:
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(
            [
                "candidate_rank",
                "candidate",
                "evidence_level",
                "evidence_type",
                "strength",
                "observation",
                "source_url",
                "provider",
                "face_band",
                "cosine_similarity",
                "tags",
                "notes",
            ]
        )
        tags = ", ".join(inv.tags)
        notes = " | ".join(n.text for n in inv.notes)
        for c in inv.result.candidates if inv.result else []:
            for ev in c.evidence:
                writer.writerow(
                    [
                        c.rank,
                        _csv_safe(c.display_name),
                        c.assessment.level.value,
                        ev.type.value,
                        ev.strength.value,
                        _csv_safe(ev.observation),
                        ev.source_url or "",
                        ev.provider or "",
                        ev.face.match_band.value if ev.face else "",
                        f"{ev.face.cosine_similarity:.4f}" if ev.face else "",
                        _csv_safe(tags),
                        _csv_safe(notes),
                    ]
                )
        return out.getvalue()

    # ------------------------------------------------------------------- PDF
    def to_pdf(self, inv: Investigation) -> bytes:
        from fpdf import FPDF
        from fpdf.enums import WrapMode

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        width = pdf.w - pdf.l_margin - pdf.r_margin
        for raw in self.to_markdown(inv).splitlines():
            line = raw.encode("latin-1", "replace").decode("latin-1")
            if line.startswith("# "):
                pdf.set_font("Helvetica", "B", 16)
                pdf.multi_cell(width, 8, line[2:])
            elif line.startswith("## "):
                pdf.ln(2)
                pdf.set_font("Helvetica", "B", 13)
                pdf.multi_cell(width, 7, line[3:])
            elif line.startswith("### "):
                pdf.set_font("Helvetica", "B", 11)
                pdf.multi_cell(width, 6, line[4:])
            elif line.startswith("|---"):
                continue
            elif line.strip():
                pdf.set_font("Helvetica", "", 9)
                text = line.replace("**", "").replace("`", "").strip("> ")
                if text.startswith("|"):
                    text = "  ".join(cell.strip() for cell in text.strip("|").split("|"))
                pdf.multi_cell(width, 5, text, wrapmode=WrapMode.CHAR)
            else:
                pdf.ln(2)
        return bytes(pdf.output())

    # ------------------------------------------------------------------- ZIP
    def to_zip(self, inv: Investigation) -> bytes:
        buf = io.BytesIO()
        name = _filename(inv)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{name}.json", self.to_json(inv))
            zf.writestr(f"{name}.md", self.to_markdown(inv))
            zf.writestr(f"{name}.html", self.to_html(inv))
            zf.writestr(f"{name}_evidence.csv", self.to_csv(inv))
            with contextlib.suppress(ImportError):  # fpdf2 not installed
                zf.writestr(f"{name}.pdf", self.to_pdf(inv))
        return buf.getvalue()

    def render(self, inv: Investigation, fmt: str) -> tuple[bytes, str, str]:
        """Return (content, media_type, filename)."""
        name = _filename(inv)
        if fmt == "json":
            return self.to_json(inv), "application/json", f"{name}.json"
        if fmt in ("md", "markdown"):
            return self.to_markdown(inv).encode(), "text/markdown; charset=utf-8", f"{name}.md"
        if fmt == "html":
            return self.to_html(inv).encode(), "text/html; charset=utf-8", f"{name}.html"
        if fmt == "csv":
            return self.to_csv(inv).encode(), "text/csv; charset=utf-8", f"{name}_evidence.csv"
        if fmt == "pdf":
            return self.to_pdf(inv), "application/pdf", f"{name}.pdf"
        if fmt == "zip":
            return self.to_zip(inv), "application/zip", f"{name}.zip"
        raise ValueError(f"Unsupported export format '{fmt}'")


def _csv_safe(value: str) -> str:
    """Neutralise spreadsheet formula injection."""
    return "'" + value if value and value[0] in "=+-@\t\r" else value
