import json

from typer.testing import CliRunner

from trueparse.cli.main import app

runner = CliRunner()


def test_cli_plain_markdown_by_default(sample_pdf_path):
    result = runner.invoke(app, ["parse", str(sample_pdf_path), "--ocr", "never"])
    assert result.exit_code == 0, result.output
    assert "Revenue" in result.stdout


def test_cli_saves_only_when_requested(sample_pdf_path, tmp_path):
    target = tmp_path / "report"
    result = runner.invoke(
        app,
        [
            "parse",
            str(sample_pdf_path),
            "--output",
            str(target),
            "--format",
            "json",
            "--format",
            "text",
            "--ocr",
            "never",
        ],
    )
    assert result.exit_code == 0, result.output
    assert {path.name for path in target.iterdir()} == {"sample_document.json", "sample_document.txt"}
    second = runner.invoke(app, ["parse", str(sample_pdf_path), "--output", str(target)])
    assert second.exit_code == 1
    assert "not empty" in second.output


def test_cli_json_envelope_and_version(sample_pdf_path):
    result = runner.invoke(app, ["parse", str(sample_pdf_path), "--format", "json", "--ocr", "never"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["json"]["schema_version"] == "3.0"
    assert "0.2.0" in runner.invoke(app, ["--version"]).stdout


def test_cli_inspect(sample_pdf_path):
    result = runner.invoke(app, ["inspect", str(sample_pdf_path)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["page_count"] == 3
