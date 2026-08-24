from __future__ import annotations
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

from trueparse.core.config import ParseOptions
from trueparse.core.enums import ParsingProfile
from trueparse.pdf.inspector import PDFInspector
from trueparse.pipeline.runner import PDFParser

app = typer.Typer(
    name="trueparse",
    help="Local-first PDF document parsing & intelligence engine.",
    add_completion=False,
)
console = Console()


@app.command("inspect")
def inspect(
    file_path: Path = typer.Argument(..., help="Path to PDF file to inspect"),
):
    """Run fast PDF forensics without full parsing."""
    try:
        inspection = PDFInspector.inspect(file_path)
        console.print(f"\n[bold green]Document Inspection Summary[/bold green] ({inspection.document_id})")
        console.print(f"File: {file_path}")
        console.print(f"Pages: {inspection.page_count} | Size: {inspection.file_size_bytes / (1024*1024):.2f} MB")
        console.print(f"SHA-256: {inspection.sha256}")
        console.print(f"Overall Native Text: [cyan]{inspection.overall_native_text}[/cyan] | Likely Scan: [yellow]{inspection.overall_likely_scan}[/yellow]\n")

        table = Table(title="Page-by-Page Forensics")
        table.add_column("Page", justify="center")
        table.add_column("Dimensions", justify="center")
        table.add_column("Native Text", justify="center")
        table.add_column("Words", justify="right")
        table.add_column("Images", justify="right")
        table.add_column("Drawings", justify="right")
        table.add_column("Scan?", justify="center")

        for p in inspection.pages:
            table.add_row(
                str(p.page_number),
                f"{p.width:.0f}x{p.height:.0f}",
                "[green]YES[/green]" if p.has_native_text else "[dim]NO[/dim]",
                str(p.word_count),
                str(p.embedded_images),
                str(p.drawing_count),
                "[yellow]YES[/yellow]" if p.likely_scan else "NO",
            )
        console.print(table)

    except Exception as e:
        console.print(f"[bold red]Inspection failed:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("parse")
def parse(
    file_path: Path = typer.Argument(..., help="Path to PDF file to parse"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output directory"),
    profile: ParsingProfile = typer.Option(ParsingProfile.BALANCED, "--profile", "-p", help="Parsing profile"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug page rendering"),
    max_pages: Optional[int] = typer.Option(None, "--max-pages", "-m", help="Limit number of pages"),
):
    """Parse a PDF into canonical document.json and extracted assets."""
    try:
        console.print(f"[bold cyan]Parsing PDF:[/bold cyan] {file_path}")
        options = ParseOptions(
            profile=profile,
            debug=debug,
            output_path=str(output) if output else "data/output",
            max_pages=max_pages,
        )
        parser = PDFParser(options=options)
        doc = parser.parse(file_path)

        out_dir = parser.storage.get_document_dir(doc.id)
        console.print(f"\n[bold green]Parsing Completed Successfully![/bold green]")
        console.print(f"Document ID: [bold]{doc.id}[/bold]")
        console.print(f"Pages Parsed: {len(doc.pages)}")
        console.print(f"Assets Extracted: {len(doc.assets)}")
        console.print(f"Sections Detected: {len(doc.sections)}")
        console.print(f"Quality Score: {doc.quality.overall_score:.2f}")
        console.print(f"Output Location: [link=file://{out_dir.resolve()}]{out_dir.resolve()}[/link]")

    except Exception as e:
        console.print(f"[bold red]Parsing failed:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("parse-async")
def parse_async(
    file_path: Path = typer.Argument(..., help="Path to PDF file to parse asynchronously"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output directory"),
    profile: ParsingProfile = typer.Option(ParsingProfile.BALANCED, "--profile", "-p", help="Parsing profile"),
):
    """Enqueue a PDF parsing task asynchronously and track progress."""
    import time
    from trueparse.workers.manager import JobManager

    if not file_path.exists():
        console.print(f"[bold red]Error:[/bold red] File not found: {file_path}")
        raise typer.Exit(code=1)

    options = ParseOptions(
        profile=profile,
        output_path=str(output) if output else "data/output",
    )
    manager = JobManager.get_instance()
    job = manager.submit_job(file_path=file_path, options=options)
    console.print(f"[bold green]Job Submitted:[/bold green] {job.job_id} for [cyan]{file_path.name}[/cyan]")

    with console.status("[bold blue]Processing in background queue...[/bold blue]") as status:
        while True:
            j = manager.get_job_status(job.job_id)
            if not j:
                break
            if j.status == "completed":
                console.print(f"\n[bold green]Job {job.job_id} Completed Successfully![/bold green]")
                if j.result:
                    console.print(f"Document ID: [bold]{j.result.get('document_id')}[/bold]")
                    console.print(f"Pages: {j.result.get('page_count')} | Quality: {j.result.get('quality_score'):.2f}")
                    console.print(f"Output JSON: {j.result.get('document_path')}")
                break
            elif j.status == "failed":
                console.print(f"\n[bold red]Job {job.job_id} Failed:[/bold red] {j.error}")
                raise typer.Exit(code=1)
            time.sleep(0.5)


@app.command("batch")
def batch(
    folder_path: Path = typer.Argument(..., help="Directory containing PDF files to parse in parallel"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output directory"),
    profile: ParsingProfile = typer.Option(ParsingProfile.BALANCED, "--profile", "-p", help="Parsing profile"),
):
    """Batch-parse all PDF documents in a directory in parallel using local worker queue."""
    import time
    from trueparse.workers.manager import JobManager

    if not folder_path.exists() or not folder_path.is_dir():
        console.print(f"[bold red]Error:[/bold red] Directory not found: {folder_path}")
        raise typer.Exit(code=1)

    pdf_files = list(folder_path.glob("*.pdf"))
    if not pdf_files:
        console.print(f"[yellow]No PDF files found in {folder_path}[/yellow]")
        return

    console.print(f"[bold cyan]Submitting batch of {len(pdf_files)} PDFs to queue...[/bold cyan]")
    options = ParseOptions(
        profile=profile,
        output_path=str(output) if output else "data/output",
    )
    manager = JobManager.get_instance()
    file_items = [(p, p.name) for p in pdf_files]
    batch_id, jobs = manager.submit_batch(file_items=file_items, options=options)

    console.print(f"[bold green]Batch ID:[/bold green] {batch_id}")

    with console.status("[bold blue]Processing batch in parallel...[/bold blue]") as status:
        while True:
            batch_jobs = manager.get_batch_status(batch_id)
            done = sum(1 for j in batch_jobs if j.status in ("completed", "failed"))
            status.update(f"[bold blue]Processing batch ({done}/{len(batch_jobs)} completed)...[/bold blue]")
            if done == len(batch_jobs):
                break
            time.sleep(0.5)

    console.print(f"\n[bold green]Batch {batch_id} Processing Finished![/bold green]")
    table = Table(title="Batch Execution Summary")
    table.add_column("File", justify="left")
    table.add_column("Status", justify="center")
    table.add_column("Doc ID", justify="center")
    table.add_column("Pages", justify="right")
    table.add_column("Score", justify="right")

    for j in manager.get_batch_status(batch_id):
        status_style = "[green]COMPLETED[/green]" if j.status == "completed" else "[red]FAILED[/red]"
        doc_id = (j.result or {}).get("document_id", "-") if j.result else "-"
        pages = str((j.result or {}).get("page_count", "-")) if j.result else "-"
        score = f"{(j.result or {}).get('quality_score', 0):.2f}" if j.result else "-"
        table.add_row(j.source_file, status_style, doc_id, pages, score)

    console.print(table)


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host address to bind"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload for development"),
):
    """Start the TrueParse REST API server with interactive Swagger documentation."""
    import uvicorn

    console.print(f"[bold green]Starting TrueParse API server[/bold green] on [cyan]http://{host}:{port}[/cyan]")
    console.print(f"Interactive Swagger Docs: [link=http://{host}:{port}/docs]http://{host}:{port}/docs[/link]\n")
    uvicorn.run("trueparse.api.routes:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()

