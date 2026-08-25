from __future__ import annotations

import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from trueparse.core.config import ParseOptions
from trueparse.core.enums import ChunkStrategy, OCRMode, ParsingProfile
from trueparse.core.version import get_version
from trueparse.pdf.inspector import PDFInspector
from trueparse.pipeline.runner import PDFParser

app = typer.Typer(
    name="trueparse",
    help="Local-first PDF document parsing & intelligence engine.",
    add_completion=False,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"trueparse {get_version()}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V",
        help="Show the installed TrueParse version and exit.",
        callback=_version_callback, is_eager=True,
    ),
):
    """Local-first PDF document parsing & intelligence engine."""


@app.command("inspect")
def inspect(
    file_path: Path = typer.Argument(..., help="Path to PDF file to inspect"),
    password: str | None = typer.Option(
        None, "--password", help="Password for an encrypted PDF"
    ),
):
    """Run fast PDF forensics without full parsing."""
    try:
        inspection = PDFInspector.inspect(file_path, password=password)
        console.print(f"\n[bold green]Document Inspection Summary[/bold green] ({inspection.document_id})")
        console.print(f"File: {file_path}")
        console.print(f"Pages: {inspection.page_count} | Size: {inspection.file_size_bytes / (1024*1024):.2f} MB")
        console.print(f"SHA-256: {inspection.sha256}")
        if inspection.is_encrypted:
            console.print("[yellow]Encrypted: YES (unlocked successfully)[/yellow]")
        console.print(
            f"Overall Native Text: [cyan]{inspection.overall_native_text}[/cyan] | "
            f"Likely Scan: [yellow]{inspection.overall_likely_scan}[/yellow]\n"
        )

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
        raise typer.Exit(code=1) from e


@app.command("parse")
def parse(
    file_path: Path = typer.Argument(..., help="Path to PDF file to parse"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Output directory"),
    profile: ParsingProfile = typer.Option(
        ParsingProfile.BALANCED, "--profile", "-p",
        help="Parsing profile: fast, balanced, accurate, maximum_accuracy",
    ),
    ocr: OCRMode = typer.Option(
        OCRMode.AUTO, "--ocr",
        help="OCR mode: auto, always, never. Needs: pip install trueparse[ocr]",
    ),
    password: str | None = typer.Option(
        None, "--password", help="Password for an encrypted PDF"
    ),
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug page rendering"),
    max_pages: int | None = typer.Option(None, "--max-pages", "-m", help="Limit number of pages"),
    chunks: bool = typer.Option(
        False, "--chunks", help="Also write chunks.jsonl for RAG ingestion"
    ),
    chunk_strategy: ChunkStrategy = typer.Option(
        ChunkStrategy.HYBRID, "--chunk-strategy", help="Chunking strategy"
    ),
    chunk_size: int = typer.Option(512, "--chunk-size", help="Approximate tokens per chunk"),
    chunk_overlap: int = typer.Option(64, "--overlap", help="Approximate overlap tokens"),
    html: bool = typer.Option(False, "--html", help="Also write a standalone document.html"),
    text: bool = typer.Option(False, "--text", help="Also write a plain document.txt"),
):
    """Parse a PDF into canonical document.json and extracted assets."""
    try:
        console.print(f"[bold cyan]Parsing PDF:[/bold cyan] {file_path}")
        options = ParseOptions(
            profile=profile,
            ocr=ocr,
            password=password,
            debug=debug,
            output_path=str(output) if output else "data/output",
            max_pages=max_pages,
            emit_chunks=chunks,
            chunk_strategy=chunk_strategy,
            chunk_max_tokens=chunk_size,
            chunk_overlap_tokens=chunk_overlap,
            emit_html=html,
            emit_text=text,
        )
        parser = PDFParser(options=options)
        doc = parser.parse(file_path)

        out_dir = parser.storage.get_document_dir(doc.id)
        console.print("\n[bold green]Parsing Completed Successfully![/bold green]")
        console.print(f"Document ID: [bold]{doc.id}[/bold]")
        console.print(f"Pages Parsed: {len(doc.pages)}")
        console.print(f"Assets Extracted: {len(doc.assets)}")
        console.print(f"Sections Detected: {len(doc.sections)}")
        console.print(
            f"Quality Score: {doc.quality.overall_score:.2f} "
            f"(text {doc.quality.text_score:.2f} / layout {doc.quality.layout_score:.2f} "
            f"/ tables {doc.quality.table_score:.2f})"
        )
        if doc.quality.ocr_pages:
            console.print(f"OCR Applied To: {doc.quality.ocr_pages} page(s)")
        console.print(f"Output Location: [link=file://{out_dir.resolve()}]{out_dir.resolve()}[/link]")

    except Exception as e:
        console.print(f"[bold red]Parsing failed:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command("chunk")
def chunk(
    document_json: Path = typer.Argument(..., help="Path to an existing document.json"),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Destination .jsonl file (default: alongside the input)"
    ),
    strategy: ChunkStrategy = typer.Option(
        ChunkStrategy.HYBRID, "--strategy", "-s", help="Chunking strategy"
    ),
    chunk_size: int = typer.Option(512, "--chunk-size", help="Approximate tokens per chunk"),
    overlap: int = typer.Option(64, "--overlap", help="Approximate overlap tokens"),
):
    """Chunk an already-parsed document.json into retrieval-ready JSONL.

    Useful for re-chunking at a different size without re-parsing the PDF.
    """
    from trueparse.chunking.chunker import DocumentChunker
    from trueparse.serializer.json import JSONSerializer

    if not document_json.exists():
        console.print(f"[bold red]Error:[/bold red] File not found: {document_json}")
        raise typer.Exit(code=1)

    try:
        doc = JSONSerializer.deserialize(document_json.read_text(encoding="utf-8"))
        chunks = DocumentChunker.chunk(
            doc, strategy=strategy, max_tokens=chunk_size, overlap_tokens=overlap
        )
        destination = output or document_json.with_name("chunks.jsonl")
        destination.write_text(
            DocumentChunker.to_jsonl(chunks) + "\n", encoding="utf-8"
        )

        tokens = [c.token_estimate for c in chunks] or [0]
        console.print(f"[bold green]Wrote {len(chunks)} chunks[/bold green] to {destination}")
        console.print(
            f"Token estimates: min {min(tokens)} / mean {sum(tokens) // len(tokens)} / max {max(tokens)}"
        )
    except Exception as e:
        console.print(f"[bold red]Chunking failed:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command("parse-async")
def parse_async(
    file_path: Path = typer.Argument(..., help="Path to PDF file to parse asynchronously"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Output directory"),
    profile: ParsingProfile = typer.Option(
        ParsingProfile.BALANCED, "--profile", "-p", help="Parsing profile"
    ),
    ocr: OCRMode = typer.Option(OCRMode.AUTO, "--ocr", help="OCR mode: auto, always, never"),
    chunks: bool = typer.Option(False, "--chunks", help="Also write chunks.jsonl"),
):
    """Enqueue a PDF parsing task asynchronously and track progress."""
    from trueparse.workers.manager import JobManager

    if not file_path.exists():
        console.print(f"[bold red]Error:[/bold red] File not found: {file_path}")
        raise typer.Exit(code=1)

    options = ParseOptions(
        profile=profile,
        ocr=ocr,
        output_path=str(output) if output else "data/output",
        emit_chunks=chunks,
    )
    manager = JobManager.get_instance()
    job = manager.submit_job(file_path=file_path, options=options)
    console.print(f"[bold green]Job Submitted:[/bold green] {job.job_id} for [cyan]{file_path.name}[/cyan]")

    with console.status("[bold blue]Processing in background queue...[/bold blue]"):
        while True:
            j = manager.get_job_status(job.job_id)
            if not j:
                break
            if j.status == "completed":
                console.print(f"\n[bold green]Job {job.job_id} Completed Successfully![/bold green]")
                if j.result:
                    console.print(f"Document ID: [bold]{j.result.get('document_id')}[/bold]")
                    console.print(
                        f"Pages: {j.result.get('page_count')} | "
                        f"Quality: {j.result.get('quality_score', 0):.2f}"
                    )
                    console.print(f"Output JSON: {j.result.get('document_path')}")
                break
            if j.status == "failed":
                console.print(f"\n[bold red]Job {job.job_id} Failed:[/bold red] {j.error}")
                raise typer.Exit(code=1)
            time.sleep(0.5)


@app.command("batch")
def batch(
    folder_path: Path = typer.Argument(..., help="Directory containing PDF files to parse in parallel"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Output directory"),
    profile: ParsingProfile = typer.Option(
        ParsingProfile.BALANCED, "--profile", "-p", help="Parsing profile"
    ),
    ocr: OCRMode = typer.Option(OCRMode.AUTO, "--ocr", help="OCR mode: auto, always, never"),
    chunks: bool = typer.Option(False, "--chunks", help="Also write chunks.jsonl per document"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Include PDFs in subdirectories"),
):
    """Batch-parse all PDF documents in a directory in parallel using local worker queue."""
    from trueparse.workers.manager import JobManager

    if not folder_path.exists() or not folder_path.is_dir():
        console.print(f"[bold red]Error:[/bold red] Directory not found: {folder_path}")
        raise typer.Exit(code=1)

    pattern = "**/*.pdf" if recursive else "*.pdf"
    pdf_files = sorted(p for p in folder_path.glob(pattern) if p.is_file())
    if not pdf_files:
        console.print(f"[yellow]No PDF files found in {folder_path}[/yellow]")
        return

    console.print(f"[bold cyan]Submitting batch of {len(pdf_files)} PDFs to queue...[/bold cyan]")
    options = ParseOptions(
        profile=profile,
        ocr=ocr,
        output_path=str(output) if output else "data/output",
        emit_chunks=chunks,
    )
    manager = JobManager.get_instance()
    batch_id, jobs = manager.submit_batch(
        file_items=[(p, p.name) for p in pdf_files], options=options
    )

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

    failures = 0
    for j in manager.get_batch_status(batch_id):
        if j.status != "completed":
            failures += 1
        status_style = "[green]COMPLETED[/green]" if j.status == "completed" else "[red]FAILED[/red]"
        result = j.result or {}
        table.add_row(
            j.source_file,
            status_style,
            str(result.get("document_id", "-")),
            str(result.get("page_count", "-")),
            f"{result.get('quality_score', 0):.2f}" if result else "-",
        )

    console.print(table)
    if failures:
        console.print(f"[bold red]{failures} document(s) failed.[/bold red]")
        raise typer.Exit(code=1)


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host address to bind"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload for development"),
):
    """Start the TrueParse REST API server with interactive Swagger documentation."""
    import uvicorn

    from trueparse.core.security import api_key

    console.print(f"[bold green]Starting TrueParse API server[/bold green] on [cyan]http://{host}:{port}[/cyan]")
    console.print(f"Interactive Swagger Docs: [link=http://{host}:{port}/docs]http://{host}:{port}/docs[/link]")
    if host not in ("127.0.0.1", "localhost") and api_key() is None:
        console.print(
            "[bold yellow]Warning:[/bold yellow] binding a non-loopback address without "
            "authentication. Set TRUEPARSE_API_KEY to require an X-API-Key header."
        )
    console.print()
    uvicorn.run("trueparse.api.routes:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
