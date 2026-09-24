"""Command-line interface. Uses exactly the same InvestigationService as the API."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from app import __version__
from app.config import get_settings
from app.domain.identity import IdentityHints, InvestigationOptions
from app.domain.investigation import EventType, InvestigationResult
from app.services.context import ProgressSink

cli = typer.Typer(help="Person Intel Agent — image-first candidate discovery and verification", no_args_is_help=True)
console = Console()

_LEVEL_STYLE = {
    "VERY_STRONG": "bold green",
    "STRONG": "green",
    "MODERATE": "yellow",
    "WEAK": "dim",
    "INSUFFICIENT": "dim",
}
_QUIET_EVENTS = {EventType.PROVIDER_FINISHED, EventType.CANDIDATE_IMAGE_DOWNLOADED, EventType.CANDIDATE_PAGE_DISCOVERED}


def _console_sink(verbose: bool) -> ProgressSink:
    def sink(type_: EventType, message: str, _data: dict[str, Any]) -> None:
        if type_ in _QUIET_EVENTS and not verbose:
            return
        style = "yellow" if type_ == EventType.WARNING else "cyan"
        console.print(f"[{style}]{type_.value:<28}[/{style}] {message}")

    return sink


@cli.command()
def investigate(
    image: list[Path] = typer.Option(
        [], "--image", "-i", exists=True, dir_okay=False, help="Reference photo (repeatable)"
    ),
    name: str | None = typer.Option(None, "--name", "-n"),
    location: str | None = typer.Option(None, "--location", "-l"),
    country: str | None = typer.Option(None, "--country"),
    username: list[str] = typer.Option([], "--username", "-u"),
    email: list[str] = typer.Option([], "--email", "-e"),
    employer: str | None = typer.Option(None, "--employer"),
    university: str | None = typer.Option(None, "--university"),
    profession: str | None = typer.Option(None, "--profession"),
    url: list[str] = typer.Option([], "--url", help="Known profile/website URL (repeatable)"),
    face: str | None = typer.Option(None, "--face", help="Target face id (e.g. f2) when several faces are detected"),
    no_reverse: bool = typer.Option(False, "--no-reverse-image", help="Skip reverse image providers"),
    export: list[str] = typer.Option([], "--export", "-x", help="Export format(s): json, md, html, csv, pdf, zip"),
    out_dir: Path = typer.Option(Path("."), "--out-dir", "-o"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run an investigation from photo(s) and/or identity hints."""
    from app.container import build_container

    hints = IdentityHints(
        name=name,
        location=location,
        country=country,
        usernames=username,
        emails=email,
        employer=employer,
        university=university,
        profession=profession,
        known_urls=url,
    )
    if not image and hints.is_empty():
        console.print("[red]Provide --image and/or at least one identity hint.[/red]")
        raise typer.Exit(2)

    async def run() -> int:
        container = build_container()
        try:
            container.retention.purge()
            inv = container.investigations.create(hints, InvestigationOptions(use_reverse_image=not no_reverse))
            for path in image:
                ref = await container.investigations.add_reference_image(inv.id, path.name, path.read_bytes())
                console.print(
                    f"📷 {path.name}: {ref.width}×{ref.height}, quality [bold]{ref.quality.label.value}[/bold], "
                    f"{len(ref.faces)} face(s), target {ref.selected_face_id or '—'}"
                )
                for issue in ref.warnings:
                    console.print(f"   [yellow]⚠ {issue}[/yellow]")
                if face and any(f.id == face for f in ref.faces):
                    container.reference_images.select_face(inv.id, ref.id, face)
            result = await container.investigations.investigate(inv.id, progress=_console_sink(verbose))
            _print_result(result)
            full = container.repo.get(inv.id)
            assert full is not None
            out_dir.mkdir(parents=True, exist_ok=True)
            for fmt in export:
                content, _media, filename = container.reports.render(full, fmt)
                (out_dir / filename).write_bytes(content)
                console.print(f"📄 {fmt}: {out_dir / filename}")
            console.print(f"\nInvestigation id: [bold]{inv.id}[/bold] (persisted; visible in the web UI)")
            return 0
        finally:
            await container.aclose()

    raise typer.Exit(asyncio.run(run()))


def _print_result(result: InvestigationResult) -> None:
    console.print(f"\n[bold]{result.conclusion}[/bold]\n")
    table = Table(title="Candidates (evidence levels are descriptive, not probabilities)")
    for col in ("#", "Candidate", "Evidence", "Face", "Image", "Name", "Location", "Sources"):
        table.add_column(col)
    for c in result.candidates[:15]:
        a = c.assessment
        table.add_row(
            str(c.rank),
            c.display_name,
            f"[{_LEVEL_STYLE[a.level.value]}]{a.level.value}[/]",
            a.face_band.value if a.face_band else "—",
            a.image_occurrence.value if a.image_occurrence else "—",
            a.name_match.value,
            a.location_match.value,
            str(len(c.urls)),
        )
    console.print(table)
    for c in result.candidates[:5]:
        console.print(f"\n[bold]{c.rank}. {c.display_name}[/bold] — {c.assessment.level.value}")
        for reason in c.assessment.reasons:
            console.print(f"   • {reason}")
        for url in c.urls[:5]:
            console.print(f"   ↳ {url}")
    for warning in result.warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")


@cli.command()
def serve(
    host: str | None = typer.Option(None, "--host", help="Bind address (default from HOST, 127.0.0.1)"),
    port: int | None = typer.Option(None, "--port", "-p"),
    dev: bool = typer.Option(False, "--dev", help="Auto-reload (development only)"),
) -> None:
    """Start the web application."""
    import uvicorn

    settings = get_settings()
    if dev and settings.is_production:
        console.print("[red]--dev is not allowed when APP_ENV=production[/red]")
        raise typer.Exit(2)
    uvicorn.run(
        "app.api.app:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=dev,
        log_level=settings.log_level.lower(),
        proxy_headers=settings.is_production,
        server_header=False,
    )


@cli.command()
def purge() -> None:
    """Apply the retention policy now (reference images, embeddings, thumbnails, cache)."""
    from app.container import build_container

    async def run() -> dict[str, int]:
        container = build_container()
        try:
            return container.retention.purge()
        finally:
            await container.aclose()

    console.print(asyncio.run(run()))


@cli.command("face-compare")
def face_compare(
    image_a: Path = typer.Argument(..., exists=True), image_b: Path = typer.Argument(..., exists=True)
) -> None:
    """Compare the largest faces of two local images (debug helper)."""
    import numpy as np

    from app.container import build_container
    from app.vision.image_validation import validate_image_bytes

    async def run() -> None:
        container = build_container()
        try:
            faces = container.faces
            if not faces.matching_available:
                console.print(f"[red]{faces.unavailable_reason}[/red]")
                return
            embeddings = []
            for path in (image_a, image_b):
                validated = validate_image_bytes(path.read_bytes(), max_bytes=container.settings.max_upload_bytes)
                found = await faces.detect_and_embed(np.asarray(validated.image))
                if not found:
                    console.print(f"[red]No face in {path}[/red]")
                    return
                embedding = max(found, key=lambda f: f.w * f.h).embedding
                if embedding is None:
                    console.print(f"[red]No embedding for {path}[/red]")
                    return
                embeddings.append(embedding)
            cmp = faces.compare(embeddings[0], embeddings[1])
            console.print(
                f"Model {faces.model_name}: cosine similarity {cmp.cosine_similarity:.4f}, "
                f"distance {cmp.distance:.4f} → band [bold]{cmp.band.value}[/bold] (not a probability)"
            )
        finally:
            await container.aclose()

    asyncio.run(run())


@cli.command("import-cookies")
def import_cookies(platform: str, cookies_file: Path = typer.Argument(..., exists=True)) -> None:
    """Import browser cookies (JSON array) for LinkedIn/Xing/… (requires SESSION_PERSISTENCE_ENABLED)."""
    from app.infrastructure.security.session_store import SessionStore

    settings = get_settings()
    if not settings.session_persistence_enabled:
        console.print(
            "[yellow]SESSION_PERSISTENCE_ENABLED is false — cookies would be lost when this command exits.[/yellow]"
        )
        raise typer.Exit(2)
    count = SessionStore(settings).save(platform, json.loads(cookies_file.read_text()))
    console.print(f"Stored {count} cookies for {platform}.")


@cli.command()
def version() -> None:
    console.print(__version__)


def main() -> None:
    sys.exit(cli())


if __name__ == "__main__":
    main()
