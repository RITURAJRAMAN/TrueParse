import json

from typer.testing import CliRunner

from trueparse.cli.main import app
from trueparse.core.version import get_version

runner = CliRunner()


def test_cli_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert get_version() in result.stdout


def test_cli_inspect(sample_pdf_path):
    result = runner.invoke(app, ["inspect", str(sample_pdf_path)])
    assert result.exit_code == 0
    assert "Document Inspection Summary" in result.stdout
    assert "Page-by-Page Forensics" in result.stdout


def test_cli_inspect_missing_file_exits_nonzero(tmp_path):
    result = runner.invoke(app, ["inspect", str(tmp_path / "nope.pdf")])
    assert result.exit_code == 1
    assert "Inspection failed" in result.stdout


def test_cli_inspect_encrypted_needs_password(encrypted_pdf_path):
    without = runner.invoke(app, ["inspect", str(encrypted_pdf_path)])
    assert without.exit_code == 1

    with_password = runner.invoke(
        app, ["inspect", str(encrypted_pdf_path), "--password", "s3cret"]
    )
    assert with_password.exit_code == 0
    assert "Encrypted: YES" in with_password.stdout


def test_cli_parse(tmp_path, sample_pdf_path):
    output_dir = tmp_path / "cli_out"
    result = runner.invoke(
        app, ["parse", str(sample_pdf_path), "-o", str(output_dir), "--max-pages", "2"]
    )
    assert result.exit_code == 0
    assert "Parsing Completed Successfully!" in result.stdout
    assert "Quality Score:" in result.stdout


def test_cli_parse_emits_extra_formats(tmp_path, sample_pdf_path):
    output_dir = tmp_path / "cli_formats"
    result = runner.invoke(app, [
        "parse", str(sample_pdf_path), "-o", str(output_dir),
        "--max-pages", "2", "--chunks", "--html", "--text",
    ])
    assert result.exit_code == 0

    doc_dirs = list(output_dir.iterdir())
    assert len(doc_dirs) == 1
    output = doc_dirs[0] / "output"
    for name in ("document.json", "document.md", "document.html", "document.txt", "chunks.jsonl"):
        assert (output / name).exists(), f"{name} missing"


def test_cli_parse_accepts_profile(tmp_path, sample_pdf_path):
    result = runner.invoke(app, [
        "parse", str(sample_pdf_path), "-o", str(tmp_path / "fast"),
        "--profile", "fast", "--max-pages", "1",
    ])
    assert result.exit_code == 0


def test_cli_chunk_reprocesses_existing_json(tmp_path, sample_pdf_path):
    output_dir = tmp_path / "cli_chunk"
    runner.invoke(app, ["parse", str(sample_pdf_path), "-o", str(output_dir), "--max-pages", "3"])

    document_json = next(output_dir.glob("*/output/document.json"))
    destination = tmp_path / "rechunked.jsonl"

    result = runner.invoke(app, [
        "chunk", str(document_json), "-o", str(destination),
        "--chunk-size", "128", "--overlap", "16",
    ])
    assert result.exit_code == 0
    assert "Wrote" in result.stdout

    records = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines() if line]
    assert records
    assert all("section_path" in r for r in records)


def test_cli_chunk_missing_file_exits_nonzero(tmp_path):
    result = runner.invoke(app, ["chunk", str(tmp_path / "nope.json")])
    assert result.exit_code == 1
