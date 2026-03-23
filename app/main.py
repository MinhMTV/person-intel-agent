"""Person Intelligence Agent — CLI Entry Point."""

import asyncio
import sys
import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from app.models import PersonQuery, Location, PersonDossier, SearchResult, SocialProfile, ImageMatch, Source, Confidence
from app.scanners.social import SocialScanner
from app.scanners.web import WebScanner
from app.scanners.image import ImageScanner
from app.scanners.email import EmailScanner
from app.scanners.professional import ProfessionalScanner, LinkedInScraper, XingScraper
from app.scanners.advanced_image import AdvancedImageScanner
from app.scanners.reverse_image import ReverseImageScanner
from app.scanners.deep_social import DeepSocialScanner
from app.scanners.professional_intel import ProfessionalIntelScanner
from app.scanners.public_records import PublicRecordsScanner
from app.scanners.data_enrichment import DataEnrichmentScanner

console = Console()
app = typer.Typer(help="Person Intelligence Agent — Automated OSINT Dossier Generator")


@app.command()
def search(
    name: str = typer.Argument(..., help="Full name of the person to search"),
    location: list[str] = typer.Option([], "--location", "-l", help="Locations (e.g. 'Oberhausen', 'NRW')"),
    country: list[str] = typer.Option([], "--country", "-c", help="Country codes (e.g. DE, AT)"),
    username: list[str] = typer.Option([], "--username", "-u", help="Known usernames"),
    email: list[str] = typer.Option([], "--email", "-e", help="Known email addresses"),
    photo: str = typer.Option(None, "--photo", "-p", help="Path to photo for image search"),
    nicknames: list[str] = typer.Option([], "--nick", "-n", help="Nicknames"),
    scanners: str = typer.Option("social,web,email,image,advanced_image,reverse_image", "--scanners", "-s", help="Comma-separated scanners"),
    output_format: str = typer.Option("markdown", "--format", "-f", help="Output format: markdown, html, pdf, json, csv"),
    multi_format: str = typer.Option(None, "--multi-format", "-mf", help="Comma-separated output formats (e.g., markdown,json,pdf)"),
):
    """Search for a person and generate an intelligence dossier."""
    # Build query
    parts = name.split()
    query = PersonQuery(
        full_name=name,
        first_name=parts[0] if parts else None,
        last_name=parts[-1] if len(parts) > 1 else None,
        nicknames=list(nicknames),
        locations=[Location(raw=loc) for loc in location],
        countries=list(country),
        usernames=list(username),
        emails=list(email),
        photo_path=photo,
    )

    console.print(f"\n🔍 Searching for: [bold]{name}[/bold]")
    if location:
        console.print(f"📍 Locations: {', '.join(location)}")
    console.print()

    # Run scanners
    dossier = asyncio.run(_run_scanners(query, scanners))

    # Display results
    _display_results(dossier)

    # Save output
    _save_output(dossier, output_format, multi_format)


@app.command()
def login(
    platform: str = typer.Argument(..., help="Platform to log in: linkedin, xing, or both"),
):
    """Login to LinkedIn and/or Xing to enable authenticated scraping."""
    async def _login():
        if platform in ("linkedin", "both"):
            scraper = LinkedInScraper(headless=False)
            print("\n🔗 LinkedIn Login")
            await scraper.login_and_save_cookies()

        if platform in ("xing", "both"):
            scraper = XingScraper(headless=False)
            print("\n🔗 Xing Login")
            await scraper.login_and_save_cookies()

    asyncio.run(_login())


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Bind host"),
    port: int = typer.Option(8080, "--port", "-p", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (dev mode)"),
):
    """Start the FastAPI web server."""
    import uvicorn

    console.print(f"\n🚀 Starting Person Intel Agent web server on [bold]{host}:{port}[/bold]")
    console.print(f"   Dashboard: [link=http://{host}:{port}]http://{host}:{port}[/link]\n")

    uvicorn.run(
        "app.web:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


@app.command()
def face_analyze(
    image: str = typer.Argument(..., help="Path to image file"),
    backend: str = typer.Option("deepface", "--backend", "-b", help="Face backend: deepface or dlib"),
    model: str = typer.Option("ArcFace", "--model", "-m", help="DeepFace model: ArcFace, Facenet, VGG-Face, etc."),
):
    """Analyze a face image: quality, age, gender, emotion, embedding."""
    from app.scanners.face_engine import FaceEngine

    engine = FaceEngine(backend=backend, model=model)

    console.print(f"\n🔍 Analyzing: [bold]{image}[/bold]")
    console.print(f"   Backend: {backend}/{model}\n")

    # Quality assessment
    quality = engine.assess_quality(image)
    console.print(f"[bold cyan]📊 Quality Assessment[/bold cyan]")
    console.print(f"  Grade:       {quality.quality_grade} ({quality.quality_score}/100)")
    console.print(f"  Blur:        {quality.blur_score}/100")
    console.print(f"  Brightness:  {quality.brightness}")
    console.print(f"  Contrast:    {quality.contrast}")
    console.print(f"  Face angle:  {quality.face_angle}°")
    console.print(f"  Face size:   {quality.face_size_ratio}")
    console.print(f"  Usable:      {'✅' if quality.is_usable else '❌'}")
    if quality.issues:
        console.print(f"  Issues:      {', '.join(quality.issues)}")

    # Full analysis
    result = engine.analyze(image)
    console.print(f"\n[bold cyan]👤 Face Analysis[/bold cyan]")
    console.print(f"  Face detected:  {'✅' if result.face_detected else '❌'}")
    if result.age_estimate:
        console.print(f"  Age estimate:   ~{result.age_estimate}")
    if result.gender:
        console.print(f"  Gender:         {result.gender}")
    if result.emotion:
        console.print(f"  Emotion:        {result.emotion}")
    if result.embedding is not None:
        console.print(f"  Embedding dim:  {len(result.embedding)}")


@app.command()
def face_compare(
    image1: str = typer.Argument(..., help="First image"),
    image2: str = typer.Argument(..., help="Second image"),
    backend: str = typer.Option("deepface", "--backend", "-b", help="Face backend: deepface or dlib"),
    model: str = typer.Option("ArcFace", "--model", "-m", help="DeepFace model"),
    threshold: float = typer.Option(0.6, "--threshold", "-t", help="Match threshold"),
):
    """Compare two face images for similarity."""
    from app.scanners.face_engine import FaceEngine

    engine = FaceEngine(backend=backend, model=model)

    console.print(f"\n🔍 Comparing faces using {backend}/{model}")
    console.print(f"  Image 1: {image1}")
    console.print(f"  Image 2: {image2}\n")

    result = engine.compare(image1, image2, threshold)

    sim = result.get("similarity", 0)
    is_match = result.get("is_match", False)
    distance = result.get("distance", "N/A")

    console.print(f"[bold cyan]📊 Results[/bold cyan]")
    console.print(f"  Similarity:  {sim:.4f} ({sim:.1%})")
    console.print(f"  Distance:    {distance}")
    console.print(f"  Match:       {'✅ YES' if is_match else '❌ NO'}")
    console.print(f"  Backend:     {result.get('backend', 'N/A')}")

    if "error" in result:
        console.print(f"  [red]Error: {result['error']}[/red]")


@app.command()
def face_batch(
    reference: str = typer.Argument(..., help="Reference image to compare against"),
    candidates_dir: str = typer.Argument(..., help="Directory with candidate images"),
    backend: str = typer.Option("deepface", "--backend", "-b", help="Face backend"),
    model: str = typer.Option("ArcFace", "--model", "-m", help="DeepFace model"),
    threshold: float = typer.Option(0.6, "--threshold", "-t", help="Match threshold"),
):
    """Batch compare a reference face against all images in a directory."""
    from app.scanners.face_engine import FaceEngine
    from pathlib import Path

    engine = FaceEngine(backend=backend, model=model)

    candidates = []
    for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
        candidates.extend(Path(candidates_dir).glob(ext))

    if not candidates:
        console.print(f"[red]No images found in {candidates_dir}[/red]")
        return

    console.print(f"\n🔍 Batch comparing {len(candidates)} images against reference")
    console.print(f"  Reference: {reference}")
    console.print(f"  Backend:   {backend}/{model}\n")

    results = engine.batch_compare(reference, [str(c) for c in candidates], threshold)

    table = Table(title="Face Comparison Results")
    table.add_column("Match", style="green", width=5)
    table.add_column("Image", style="cyan")
    table.add_column("Similarity", style="yellow")
    table.add_column("Quality", style="blue")

    for r in results:
        name = Path(r.get("path", "?")).name
        sim = f"{r.get('similarity', 0):.4f}"
        is_match = "✅" if r.get("is_match") else "❌"
        grade = r.get("quality_grade", "?")
        table.add_row(is_match, name, sim, grade)

    console.print(table)


@app.command()
def ai_analyze(
    name: str = typer.Argument(..., help="Full name to search and analyze"),
    location: list[str] = typer.Option([], "--location", "-l", help="Locations"),
    username: list[str] = typer.Option([], "--username", "-u", help="Known usernames"),
    email: list[str] = typer.Option([], "--email", "-e", help="Known emails"),
    nicknames: list[str] = typer.Option([], "--nick", "-n", help="Nicknames"),
    scanners: str = typer.Option("social,web,email,deep_social,professional_intel", "--scanners", "-s"),
    analysis: str = typer.Option("all", "--analysis", "-a", help="Analysis type: summary, connections, narrative, anomalies, next, all"),
):
    """Run OSINT search with AI-powered analysis."""
    from app.ai_analyzer import AIAnalyzer

    # Build query
    parts = name.split()
    query = PersonQuery(
        full_name=name,
        first_name=parts[0] if parts else None,
        last_name=parts[-1] if len(parts) > 1 else None,
        nicknames=list(nicknames),
        locations=[Location(raw=loc) for loc in location],
        usernames=list(username),
        emails=list(email),
    )

    console.print(f"\n🔍 Searching for: [bold]{name}[/bold]")
    console.print("   This includes AI analysis...\n")

    # Run scanners
    dossier = asyncio.run(_run_scanners(query, scanners))

    # Display basic results
    _display_results(dossier)

    # AI Analysis
    analyzer = AIAnalyzer()
    if not analyzer.api_key:
        console.print("\n[yellow]⚠️ No OpenAI/OpenRouter API key set. Skipping AI analysis.[/yellow]")
        console.print("   Set OPENAI_API_KEY or OPENROUTER_API_KEY environment variable.")
        return

    console.print(f"\n[bold cyan]🤖 AI Analysis[/bold cyan]")

    if analysis in ("all", "summary"):
        console.print("\n[yellow]📊 Executive Summary:[/yellow]")
        summary = analyzer.summarize(dossier)
        if summary:
            console.print(summary)
        else:
            console.print("  (LLM not available)")

    if analysis in ("all", "connections"):
        console.print("\n[yellow]🔗 Connections & Patterns:[/yellow]")
        connections = analyzer.suggest_connections(dossier)
        if connections:
            console.print(connections)

    if analysis in ("all", "narrative"):
        console.print("\n[yellow]📝 Narrative Report:[/yellow]")
        narrative = analyzer.generate_narrative_report(dossier)
        if narrative:
            console.print(narrative)

    if analysis in ("all", "anomalies"):
        console.print("\n[yellow]⚠️ Anomaly Detection:[/yellow]")
        anomalies = analyzer.detect_anomalies(dossier)
        if anomalies:
            console.print(anomalies)

    if analysis in ("all", "next"):
        console.print("\n[yellow]🎯 Suggested Next Steps:[/yellow]")
        suggestions = analyzer.suggest_next_searches(dossier)
        if suggestions:
            console.print(suggestions)


async def _run_scanners(query: PersonQuery, scanner_list: str) -> PersonDossier:
    """Run selected scanners."""
    scanner_map = {
        "social": SocialScanner(),
        "web": WebScanner(),
        "email": EmailScanner(),
        "image": ImageScanner(),
        "advanced_image": AdvancedImageScanner(),
        "reverse_image": ReverseImageScanner(),
        "deep_social": DeepSocialScanner(),
        "professional": ProfessionalScanner(headless=True),
        "professional_intel": ProfessionalIntelScanner(),
        "public_records": PublicRecordsScanner(),
        "data_enrichment": DataEnrichmentScanner(),
    }

    selected = [s.strip() for s in scanner_list.split(",")]
    scanners = [scanner_map[s] for s in selected if s in scanner_map]

    dossier = PersonDossier(query=query)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        for scanner in scanners:
            task = progress.add_task(f"Running {scanner.name}...", total=None)
            try:
                results = await scanner.scan(query)
                progress.update(task, description=f"✅ {scanner.name} — {len(results)} results")
                dossier.scanners_used.append(scanner.name)

                for r in results:
                    if isinstance(r, SocialProfile):
                        dossier.social_profiles.append(r)
                    elif isinstance(r, ImageMatch):
                        dossier.image_matches.append(r)
                    elif isinstance(r, SearchResult):
                        if r.source in (Source.EMAIL, Source.BREACH):
                            dossier.email_addresses.append(r.url.replace("mailto:", ""))
                        elif r.source in (Source.LINKEDIN, Source.XING):
                            dossier.professional.append(r)
                        else:
                            dossier.web_results.append(r)
            except Exception as e:
                progress.update(task, description=f"❌ {scanner.name} — {e}")

    dossier.total_sources_checked = len(dossier.web_results) + len(dossier.social_profiles)
    return dossier


def _display_results(dossier: PersonDossier):
    """Display results in a rich table."""
    table = Table(title=f"Dossier: {dossier.query.full_name}")
    table.add_column("Category", style="cyan")
    table.add_column("Count", style="green")
    table.add_column("Top Results")

    social_summary = ", ".join(set(p.platform for p in dossier.social_profiles[:5])) or "—"
    web_summary = dossier.web_results[0].title[:40] if dossier.web_results else "—"

    table.add_row("Social Profiles", str(len(dossier.social_profiles)), social_summary)
    table.add_row("Web Results", str(len(dossier.web_results)), web_summary)
    table.add_row("Image Matches", str(len(dossier.image_matches)),
                   "—" if not dossier.image_matches else f"Best: {dossier.image_matches[0].similarity_score:.0%}")
    table.add_row("Emails", str(len(dossier.email_addresses)), "—")
    table.add_row("Scanners Used", ", ".join(dossier.scanners_used), "—")

    console.print(table)

    # Detailed social profiles
    if dossier.social_profiles:
        console.print("\n[bold cyan]📱 Social Profiles Found:[/bold cyan]")
        for p in dossier.social_profiles:
            extra = f" — {p.bio[:40]}" if p.bio else ""
            console.print(f"  • [green]{p.platform}[/green]: {p.url}{extra}")

    # Detailed web results
    if dossier.web_results:
        console.print("\n[bold cyan]🌐 Web Results:[/bold cyan]")
        for r in dossier.web_results[:10]:
            console.print(f"  • {r.title[:60]}")
            console.print(f"    {r.url}")

    # Detailed image matches
    if dossier.image_matches:
        console.print("\n[bold cyan]🖼️ Image Matches (Face Recognition):[/bold cyan]")
        for m in dossier.image_matches:
            console.print(f"  • Similarity: [green]{m.similarity_score:.0%}[/green]")
            console.print(f"    Source: {m.source_url[:60]}")
            console.print(f"    Image: {m.image_url[:60]}")


def _save_output(dossier: PersonDossier, fmt: str, multi_format: str = None):
    """Save dossier to file(s) using ReportGenerator."""
    from app.report_generator import ReportGenerator

    generator = ReportGenerator()

    if multi_format:
        formats = [f.strip() for f in multi_format.split(",")]
    else:
        formats = [fmt]

    outputs = generator.generate(dossier, formats)

    from rich.console import Console
    console = Console()
    for format_name, path in outputs.items():
        console.print(f"📄 [{format_name}] Saved to: [bold]{path}[/bold]")


if __name__ == "__main__":
    app()
