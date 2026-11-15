import json

from trueparse.config.options import JsonOptions, ParseOptions
from trueparse.exporters.json import DocumentProjector
from trueparse.models.enums import ImageInclusionMode, OutputFormat
from trueparse.pipeline.runner import PDFParser


def test_projection_strips_coordinates(sample_pdf_path, tmp_path):
    """coordinates: false must recursively strip bbox objects."""
    options = ParseOptions(output_path=str(tmp_path), max_pages=1)
    parser = PDFParser(options=options)
    doc = parser.parse(sample_pdf_path)

    # 1. Full projection with coordinates
    full_opts = JsonOptions(coordinates=True)
    full_dict = DocumentProjector.project(doc, full_opts)
    assert any(s.get("bbox") for n in full_dict["nodes"].values() for s in n["source"])

    # 2. Coordinate-stripped projection
    no_coords_opts = JsonOptions(coordinates=False)
    pruned_dict = DocumentProjector.project(doc, no_coords_opts)
    assert all("bbox" not in s for n in pruned_dict["nodes"].values() for s in n["source"])

    # 3. Size reduction verification
    full_json = json.dumps(DocumentProjector.project(doc, full_opts))
    pruned_json = json.dumps(DocumentProjector.project(doc, no_coords_opts))
    assert len(pruned_json) < len(full_json)


def test_projection_base64_images(sample_pdf_path, tmp_path):
    """images: 'base64' embeds data URIs directly into assets."""
    options = ParseOptions(output_path=str(tmp_path), max_pages=2)
    options.output_config.json_options.images = ImageInclusionMode.BASE64
    parser = PDFParser(options=options)
    response = parser.parse_to_response(sample_pdf_path)

    assert response.status == "completed"
    assert response.data.document is not None
    assets = response.data.document.get("assets")
    if assets:
        for asset in assets.values():
            assert asset.get("path") is None
            if asset.get("data_uri"):
                assert asset["data_uri"].startswith("data:image/")


def test_explicit_projection_selection(sample_pdf_path, tmp_path):
    options = ParseOptions(output_path=str(tmp_path), max_pages=1)
    options.output_config.json_options.coordinates = False
    options.output_config.json_options.quality_scores = False
    parser = PDFParser(options=options)
    response = parser.parse_to_response(sample_pdf_path)

    assert OutputFormat.MARKDOWN.value in response.formats_provided
    assert response.data.markdown is not None
    # In text minimal, coordinates and quality are stripped
    assert "diagnostics" not in response.data.document
    assert all("bbox" not in s for n in response.data.document["nodes"].values() for s in n["source"])


def test_in_memory_mode_writes_no_files(sample_pdf_path, tmp_path):
    """save_to_disk: false must perform parse without creating files in output_path."""
    run_dir = tmp_path / "in_memory_run"
    options = ParseOptions(output_path=str(run_dir), max_pages=1)
    options.output_config.save_to_disk = False
    parser = PDFParser(options=options)
    response = parser.parse_to_response(sample_pdf_path)

    assert response.status == "completed"
    assert response.data.document is not None
    # Exactly zero files or directories created inside run_dir
    assert not run_dir.exists() or len(list(run_dir.iterdir())) == 0
