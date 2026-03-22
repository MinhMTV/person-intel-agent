"""Person Intelligence Agent — CLI Entry Point."""

import asyncio
import sys
import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from app.models import PersonQuery, Location, PersonDossier, SearchResult, SocialProfile, ImageMatch
from app.scanners.social import SocialScanner
from app.scanners.web import WebScanner
from app.scanners.image import ImageScanner

console = Console()


def main(
    name: str = typer.Argument(..., help="Full name of the person to search"),
    location: list[str] = typer.Option([], "--location", "-l", help="Locations (e.g. 'Oberhausen', 'NRW')"),
    country: list[str] = typer.Option([], "--country", "-c", help="Country codes (e.g. DE, AT)"),
    username: list[str] = typer.Option([], "--username", "-u", help="Known usernames"),
    email: list[str] = typer.Option([], "--email", "-e", help="Known email addresses"),
    photo: str = typer.Option(None, "--photo", "-p", help="Path to photo for image search"),
    nicknames: list[str] = typer.Option([], "--nick", "-n", help="Nicknames"),
    scanners: str = typer.Option("social,web,image", "--scanners", "-s", help="Comma-separated scanners"),
    output_format: str = typer.Option("markdown", "--format", "-f", help="Output format: markdown, pdf, json"),
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
    _save_output(dossier, output_format)


async def _run_scanners(query: PersonQuery, scanner_list: str) -> PersonDossier:
    """Run selected scanners concurrently."""
    scanner_map = {
        "social": SocialScanner(),
        "web": WebScanner(),
        "image": ImageScanner(),
    }

    selected = [s.strip() for s in scanner_list.split(",")]
    scanners = [scanner_map[s] for s in selected if s in scanner_map]

    dossier = PersonDossier(query=query)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        tasks = []
        for scanner in scanners:
            task = progress.add_task(f"Running {scanner.name}...", total=None)
            tasks.append((scanner, task))

        for scanner, task in tasks:
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
    table.add_row("Image Matches", str(len(dossier.image_matches)), "—" if not dossier.image_matches else f"Best: {dossier.image_matches[0].similarity_score:.0%}")
    table.add_row("Emails", str(len(dossier.email_addresses)), "—")
    table.add_row("Scanners Used", ", ".join(dossier.scanners_used), "—")

    console.print(table)

    # Detailed social profiles
    if dossier.social_profiles:
        console.print("\n[bold cyan]📱 Social Profiles Found:[/bold cyan]")
        for p in dossier.social_profiles:
            console.print(f"  • [green]{p.platform}[/green]: {p.url}")

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


def _save_output(dossier: PersonDossier, fmt: str):
    """Save dossier to file."""
    from pathlib import Path

    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    filename = dossier.query.full_name.replace(" ", "_").lower()

    if fmt == "json":
        path = output_dir / f"{filename}.json"
        path.write_text(dossier.model_dump_json(indent=2))
    else:
        path = output_dir / f"{filename}.md"
        path.write_text(dossier.summary())

    console.print(f"\n📄 Saved to: [bold]{path}[/bold]")


if __name__ == "__main__":
    typer.run(main)
