import json

from trueparse.config.options import JsonOptions, ParseOptions
from trueparse.exporters.json import DocumentProjector
from trueparse.models.enums import OutputFormat
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
