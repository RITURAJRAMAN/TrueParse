import httpx
import pytest

from trueparse import ParseError, TrueParseClient
from trueparse.chunking.chunker import DocumentChunker
from trueparse.config.options import ParseOptions
from trueparse.exporters.renderer import OutputRenderer
from trueparse.exporters.tables import TableRenderer
from trueparse.models.document import Document, Page
from trueparse.models.elements import TableCell, TableElement
from trueparse.models.enums import ChunkStrategy
from trueparse.runtime.timing import StageTimings


def table_document(*, headers_only=False):
    table = TableElement(
        id="table",
        page=1,
        reading_order=1,
        content="Group\nAmount",
        rows=2,
        columns=2,
        cells=[
            TableCell(id="h", row=0, column=0, col_span=2, text="Group", is_header=True),
            TableCell(id="v", row=1, column=0, text="Amount", is_header=headers_only),
        ],
    )
    return Document(id="review", pages=[Page(page_number=1, width=600, height=800, elements=[table])])


@pytest.mark.parametrize("strategy", list(ChunkStrategy))
def test_header_only_table_is_not_lost_from_chunks(strategy):
    chunks = DocumentChunker.chunk(table_document(headers_only=True), strategy=strategy)
    assert len(chunks) == 1
    assert "Group" in chunks[0].text and "Amount" in chunks[0].text
    assert chunks[0].element_ids == ["table"]
    assert any(span.row_start == 0 and span.row_end == 2 for span in chunks[0].source_spans)


def test_combined_exports_render_spanned_table_html_once(monkeypatch):
    original = TableRenderer._to_html
    calls = []

    def render(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(TableRenderer, "_to_html", render)
    options = ParseOptions(output_config={"formats": ["markdown", "html"], "save_to_disk": False})
    result = OutputRenderer(options, StageTimings()).render(table_document())
    assert 'colspan="2"' in result.markdown and 'colspan="2"' in result.html
    assert len(calls) == 1


def test_client_preserves_remote_error_context():
    payload = {
        "error": {
            "code": "PARSE_TIMEOUT",
            "message": "Deadline exceeded",
            "retryable": True,
            "request_id": "request-1",
            "document_id": "doc-1",
            "page": 2,
            "element_id": "node-1",
            "details": {"stage": "ocr", "http_status": 200},
        }
    }
    with TrueParseClient() as client:
        client._http.close()
        client._http = httpx.Client(
            base_url="http://test",
            transport=httpx.MockTransport(lambda request: httpx.Response(504, json=payload)),
        )
        with pytest.raises(ParseError) as caught:
            client.parse(b"pdf")
    error = caught.value
    assert error.retryable is True
    assert (error.request_id, error.document_id, error.page, error.element_id) == (
        "request-1",
        "doc-1",
        2,
        "node-1",
    )
    assert error.details == {"stage": "ocr", "http_status": 504}


@pytest.mark.parametrize(
    "fields",
    [
        {"message": []},
        {"details": []},
        {"retryable": "true"},
        {"page": True},
        {"document_id": {}},
    ],
)
def test_client_rejects_malformed_remote_error_fields(fields):
    payload = {"error": {"code": "PDF_PARSE_ERROR", "message": "Failed", **fields}}
    with TrueParseClient() as client:
        client._http.close()
        client._http = httpx.Client(
            base_url="http://test",
            transport=httpx.MockTransport(lambda request: httpx.Response(422, json=payload)),
        )
        with pytest.raises(ParseError) as caught:
            client.parse(b"pdf")
    assert caught.value.code.value == "PDF_PARSE_ERROR"
    assert caught.value.message == "TrueParse returned an invalid error response."
    assert caught.value.details == {"http_status": 422}
