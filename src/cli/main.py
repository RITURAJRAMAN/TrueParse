"""Thin CLI adapter around the public SDK."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import typer

from trueparse._version import get_version
from trueparse.chunking.tokenization import TokenizerName
from trueparse.config.options import ResourceLimits
from trueparse.exceptions import PDFEngineError
from trueparse.models.enums import ImageInclusionMode, OCRMode, OutputFormat, ParsingProfile
from trueparse.sdk.local import parse as parse_pdf

app = typer.Typer(
    name="trueparse",
    help="Extract PDFs locally or run the API and optional durable job workers.",
    add_completion=False,
)


def version_callback(value: bool):
    if value:
        typer.echo(get_version())
        raise typer.Exit()


@app.callback()
def main(version: bool = typer.Option(False, "--version", "-V", callback=version_callback, is_eager=True)):
    pass


@app.command("parse")
def parse(
    file: Path = typer.Argument(..., exists=True, dir_okay=False),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Save into a new or empty directory. Otherwise print the result."
    ),
    formats: list[OutputFormat] | None = typer.Option(
        None, "--format", "-f", help="Repeat for markdown, json, text, html, chunks."
    ),
    images: ImageInclusionMode = typer.Option(ImageInclusionMode.NONE, "--images"),
    profile: ParsingProfile = typer.Option(ParsingProfile.BALANCED, "--profile"),
    ocr: OCRMode = typer.Option(OCRMode.AUTO, "--ocr"),
    password: str | None = typer.Option(None, "--password"),
    max_pages: int | None = typer.Option(None, "--max-pages", min=1),
    timeout_seconds: float | None = typer.Option(None, "--timeout", min=0.001),
    coordinates: bool = typer.Option(False, "--coordinates", help="Include coordinates in document JSON."),
    chunk_size: int = typer.Option(512, "--chunk-size", min=32),
    chunk_tokenizer: str = typer.Option(
        "approximate", "--chunk-tokenizer", help="approximate, cl100k_base or o200k_base"
    ),
    strict: bool = typer.Option(False, "--strict", help="Reject known incomplete extraction."),
    max_response_mb: int = typer.Option(128, "--max-response-mb", min=1),
    max_render_pixels: int = typer.Option(25_000_000, "--max-render-pixels", min=1),
):
    try:
        result = parse_pdf(
            file,
            formats=formats or [OutputFormat.MARKDOWN],
            images=images,
            profile=profile,
            ocr=ocr,
            password=password,
            max_pages=max_pages,
            timeout_seconds=timeout_seconds,
            chunk_size=chunk_size,
            chunk_tokenizer=cast(TokenizerName, chunk_tokenizer),
            strict=strict,
            limits=ResourceLimits(
                max_response_bytes=max_response_mb * 1024 * 1024, max_render_pixels=max_render_pixels
            ),
            json_options={"coordinates": coordinates},
        )
        if output:
            typer.echo(str(result.save(output)))
        elif result.formats_provided == [OutputFormat.MARKDOWN]:
            typer.echo(result.markdown or "")
        elif result.formats_provided == [OutputFormat.TEXT]:
            typer.echo(result.text or "")
        else:
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
        for warning in result.warnings:
            typer.echo(f"Warning: {warning}", err=True)
    except (PDFEngineError, FileExistsError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command("inspect")
def inspect(
    file: Path = typer.Argument(..., exists=True, dir_okay=False),
    password: str | None = typer.Option(None, "--password"),
):
    from trueparse.extraction.pdf.inspector import PDFInspector

    try:
        typer.echo(PDFInspector.inspect(file, password=password).model_dump_json(indent=2))
    except PDFEngineError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command("serve")
def serve(host: str = "127.0.0.1", port: int = 8000):
    try:
        import uvicorn
    except ImportError as exc:
        typer.echo("Install trueparse[api] to run the HTTP service.", err=True)
        raise typer.Exit(1) from exc
    uvicorn.run("trueparse.api.app:app", host=host, port=port)


if __name__ == "__main__":
    app()
