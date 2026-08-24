from typer.testing import CliRunner
from trueparse.cli.main import app

runner = CliRunner()


def test_cli_inspect(sample_pdf_path):
    result = runner.invoke(app, ["inspect", str(sample_pdf_path)])
    assert result.exit_code == 0
    assert "Document Inspection Summary" in result.stdout
    assert "Page-by-Page Forensics" in result.stdout


def test_cli_parse(tmp_path, sample_pdf_path):
    output_dir = tmp_path / "cli_out"
    result = runner.invoke(app, ["parse", str(sample_pdf_path), "-o", str(output_dir), "--max-pages", "2"])
    assert result.exit_code == 0
    assert "Parsing Completed Successfully!" in result.stdout
