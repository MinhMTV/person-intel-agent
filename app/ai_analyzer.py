"""AI Analysis Module — LLM-powered intelligence analysis.

Features:
  - Summarize findings across all scanners
  - Suggest connections and patterns
  - Generate narrative dossier report
  - Anomaly detection
  - Predictive search suggestions
"""

from __future__ import annotations

import json
import os
from typing import Optional

from app.models import PersonDossier, PersonQuery, SocialProfile, SearchResult, ImageMatch


class AIAnalyzer:
    """LLM-powered analysis of intelligence dossiers.

    Uses OpenAI-compatible API (works with OpenRouter, OpenAI, etc.)
    for natural language analysis and report generation.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini"):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        self.model = model
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

    def _call_llm(self, prompt: str, system: str = "") -> Optional[str]:
        """Call LLM API (sync)."""
        if not self.api_key:
            return None

        try:
            import httpx

            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": 2000,
                    "temperature": 0.3,
                },
                timeout=60,
            )

            if resp.status_code == 200:
                data = resp.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content")
        except Exception as e:
            print(f"  LLM call error: {e}")

        return None

    def summarize(self, dossier: PersonDossier) -> Optional[str]:
        """Generate an executive summary of all findings."""
        findings = self._extract_findings(dossier)

        prompt = f"""Analyze this person intelligence dossier and provide a concise executive summary.

Person: {dossier.query.full_name}
Locations: {', '.join(l.raw for l in dossier.query.locations) if dossier.query.locations else 'Unknown'}

FINDINGS:
{findings}

Provide:
1. A 2-3 sentence summary of who this person likely is
2. Key findings ranked by confidence
3. Any notable patterns or connections
4. Data gaps or areas for further investigation

Keep it professional and factual. Do not speculate beyond the data."""

        return self._call_llm(prompt, system="You are an OSINT analyst writing intelligence summaries.")

    def suggest_connections(self, dossier: PersonDossier) -> Optional[str]:
        """Suggest connections and relationships between findings."""
        findings = self._extract_findings(dossier)

        prompt = f"""Analyze these OSINT findings for a person named {dossier.query.full_name}.

{findings}

Identify:
1. Connections between different platforms (same username, similar bios, matching locations)
2. Professional network clues (company associations, academic collaborations)
3. Geographic patterns (where they appear online)
4. Timeline patterns (when profiles were created, activity periods)
5. Potential aliases or alternate identities

Be specific and reference the actual data found."""

        return self._call_llm(prompt, system="You are an OSINT analyst identifying patterns and connections.")

    def generate_narrative_report(self, dossier: PersonDossier) -> Optional[str]:
        """Generate a full narrative intelligence report."""
        findings = self._extract_findings(dossier)

        prompt = f"""Write a professional intelligence dossier report for:

Subject: {dossier.query.full_name}
Scanners Used: {', '.join(dossier.scanners_used)}
Total Sources: {dossier.total_sources_checked}

RAW FINDINGS:
{findings}

Write a structured report with these sections:

## Executive Summary
(2-3 sentences on the subject)

## Digital Presence
(Social media profiles, activity levels, verified accounts)

## Professional Profile
(Employment, academic, business associations)

## Online Footprint
(Web mentions, image presence, email patterns)

## Intelligence Gaps
(What we couldn't find, what would require deeper investigation)

## Confidence Assessment
(Overall confidence level with justification)

Use professional intelligence reporting language. Be factual, cite specific findings."""

        return self._call_llm(prompt, system="You are writing an official intelligence dossier report.")

    def detect_anomalies(self, dossier: PersonDossier) -> Optional[str]:
        """Detect unusual patterns or anomalies in the data."""
        findings = self._extract_findings(dossier)

        prompt = f"""Review these OSINT findings for {dossier.query.full_name} and identify any anomalies:

{findings}

Look for:
1. Inconsistencies between profiles (different locations, ages, names)
2. Suspicious patterns (newly created accounts, minimal activity)
3. Unusual connections (disparate industries, unexpected platforms)
4. Data quality issues (unverified claims, conflicting information)
5. Privacy red flags (exposed personal information)

Be specific about what you found and why it's notable."""

        return self._call_llm(prompt, system="You are an OSINT analyst performing anomaly detection.")

    def suggest_next_searches(self, dossier: PersonDossier) -> Optional[str]:
        """Suggest next searches based on current findings."""
        findings = self._extract_findings(dossier)

        prompt = f"""Based on these OSINT findings for {dossier.query.full_name}, suggest next investigation steps:

{findings}

Suggest:
1. Specific platforms to search based on found data
2. Alternative name spellings or aliases to try
3. Geographic areas to focus on
4. Professional networks to explore
5. Image-based searches that might yield results

Be actionable and specific."""

        return self._call_llm(prompt, system="You are an OSINT analyst recommending investigation steps.")

    def _extract_findings(self, dossier: PersonDossier) -> str:
        """Extract structured findings from dossier for LLM prompt."""
        lines = []

        # Social profiles
        if dossier.social_profiles:
            lines.append("SOCIAL PROFILES:")
            for p in dossier.social_profiles:
                bio_str = f" - {p.bio[:100]}" if p.bio else ""
                followers_str = f" ({p.followers:,} followers)" if p.followers else ""
                verified_str = " [VERIFIED]" if p.verified else ""
                lines.append(f"  - {p.platform}: {p.display_name or p.username}{followers_str}{verified_str}{bio_str}")
            lines.append("")

        # Web results
        if dossier.web_results:
            lines.append("WEB MENTIONS:")
            for r in dossier.web_results[:15]:
                source = f"[{r.source.value}] " if r.source else ""
                lines.append(f"  - {source}{r.title[:80]}: {r.url}")
                if r.snippet:
                    lines.append(f"    > {r.snippet[:100]}")
            lines.append("")

        # Emails
        if dossier.email_addresses:
            lines.append("EMAIL ADDRESSES:")
            for email in dossier.email_addresses:
                lines.append(f"  - {email}")
            lines.append("")

        # Professional
        if dossier.professional:
            lines.append("PROFESSIONAL:")
            for r in dossier.professional[:10]:
                lines.append(f"  - {r.title[:80]}: {r.url}")
            lines.append("")

        # Academic
        if dossier.academic:
            lines.append("ACADEMIC:")
            for r in dossier.academic[:10]:
                lines.append(f"  - {r.title[:80]}: {r.url}")
            lines.append("")

        # Image matches
        if dossier.image_matches:
            lines.append("IMAGE MATCHES:")
            for m in dossier.image_matches[:5]:
                lines.append(f"  - {m.similarity_score:.0%} match: {m.source_url}")
            lines.append("")

        return "\n".join(lines) if lines else "No significant findings."
