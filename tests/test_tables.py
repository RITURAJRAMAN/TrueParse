import pymupdf as fitz

from trueparse.core.enums import ElementType
from trueparse.core.models import BoundingBox, TableCell, TableElement
from trueparse.tables.native import NativeTableExtractor


def _cell(row: int, col: int, text: str, x0: float, is_header: bool = False) -> TableCell:
    return TableCell(
        id=f"c{row}{col}",
        row=row,
        column=col,
        is_header=is_header,
        text=text,
        bbox=BoundingBox(x0=x0, y0=100.0 + row * 20, x1=x0 + 100, y1=120.0 + row * 20),
    )


def _table(elem_id: str, page: int, rows: int, cells: list[TableCell]) -> TableElement:
    return TableElement(
        id=elem_id,
        type=ElementType.TABLE,
        page=page,
        bbox=BoundingBox(x0=50.0, y0=100.0, x1=550.0, y1=100.0 + rows * 20),
        reading_order=1,
        content="",
        rows=rows,
        columns=2,
        cells=cells,
        markdown="| A | B |\n| --- | --- |\n| 1 | 2 |",
    )


class TestNativeTableExtraction:
    def test_extracts_a_ruled_table(self, sample_pdf_path):
        doc = fitz.open(sample_pdf_path)
        found = 0
        for page_idx in range(len(doc)):
            for table in NativeTableExtractor.extract_page_tables(doc[page_idx], page_idx + 1):
                found += 1
                assert table.type == ElementType.TABLE
                assert table.rows > 0
                assert table.columns > 0
                assert table.markdown and table.html
                assert "<table>" in table.html
        doc.close()
        assert found > 0

    def test_cell_count_accounts_for_spans(self, sample_pdf_path):
        """Cells covered by a span are omitted, so the count is <= rows*cols."""
        doc = fitz.open(sample_pdf_path)
        for page_idx in range(len(doc)):
            for table in NativeTableExtractor.extract_page_tables(doc[page_idx], page_idx + 1):
                covered = sum(c.row_span * c.col_span for c in table.cells)
                assert covered <= table.rows * table.columns
                assert len(table.cells) <= table.rows * table.columns
        doc.close()

    def test_unknown_strategy_is_ignored_gracefully(self, sample_pdf_path):
        doc = fitz.open(sample_pdf_path)
        tables = NativeTableExtractor.extract_page_tables(
            doc[1], 2, strategies=["not_a_real_strategy"]
        )
        doc.close()
        assert tables == []

    def test_text_strategy_does_not_duplicate_ruled_tables(self, sample_pdf_path):
        doc = fitz.open(sample_pdf_path)
        ruled = NativeTableExtractor.extract_page_tables(doc[1], 2, strategies=["lines"])
        both = NativeTableExtractor.extract_page_tables(doc[1], 2, strategies=["lines", "text"])
        doc.close()
        assert len(both) == len(ruled)


class TestSpanReconstruction:
    def test_colspan_is_detected_and_covered_cell_dropped(self, spanned_table_pdf_path):
        doc = fitz.open(spanned_table_pdf_path)
        tables = NativeTableExtractor.extract_page_tables(doc[0], 1, detect_spans=True)
        doc.close()

        assert len(tables) == 1
        table = tables[0]
        header = next(c for c in table.cells if c.row == 0)
        assert header.col_span == 2
        assert header.text == "Consolidated Results"
        assert not any(c.row == 0 and c.column == 1 for c in table.cells)
        assert 'colspan="2"' in table.html

    def test_spans_disabled_leaves_a_flat_grid(self, spanned_table_pdf_path):
        doc = fitz.open(spanned_table_pdf_path)
        tables = NativeTableExtractor.extract_page_tables(doc[0], 1, detect_spans=False)
        doc.close()
        assert all(c.row_span == 1 and c.col_span == 1 for c in tables[0].cells)

    def test_html_escapes_cell_content(self):
        cells = [_cell(0, 0, "<script>alert(1)</script>", 50.0, is_header=True)]
        html = NativeTableExtractor._to_html(cells, num_rows=1, header_rows=1)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_markdown_escapes_pipes_and_pads_short_rows(self):
        markdown = NativeTableExtractor._to_markdown(
            [["A|B", "C"], ["only-one-cell"]], num_cols=2
        )
        lines = markdown.split("\n")
        assert "A\\|B" in lines[0]

        def cell_count(line: str) -> int:
            return len(line.replace("\\|", "").strip().strip("|").split("|"))

        assert all(cell_count(line) == 2 for line in lines), lines


class TestCrossPageMerge:
    def test_continuation_is_absorbed(self):
        first = _table("t1", page=1, rows=2, cells=[
            _cell(0, 0, "Metric", 50.0, is_header=True),
            _cell(0, 1, "Value", 300.0, is_header=True),
            _cell(1, 0, "Revenue", 50.0),
            _cell(1, 1, "$100M", 300.0),
        ])
        second = _table("t2", page=2, rows=1, cells=[
            _cell(0, 0, "Costs", 50.0),
            _cell(0, 1, "$40M", 300.0),
        ])
        pages = [[first], [second]]

        merged = NativeTableExtractor.merge_cross_page_tables(pages)
        assert len(merged[0]) == 1
        assert len(merged[1]) == 0
        assert merged[0][0].rows == 3
        assert any(c.text == "Costs" for c in merged[0][0].cells)
        assert merged[0][0].metadata["continued_from_pages"] == [2]

    def test_repeated_header_row_is_dropped(self):
        header_cells = [
            _cell(0, 0, "Metric", 50.0, is_header=True),
            _cell(0, 1, "Value", 300.0, is_header=True),
        ]
        first = _table("t1", page=1, rows=2, cells=header_cells + [
            _cell(1, 0, "Revenue", 50.0),
            _cell(1, 1, "$100M", 300.0),
        ])
        second = _table("t2", page=2, rows=2, cells=[
            _cell(0, 0, "Metric", 50.0, is_header=True),
            _cell(0, 1, "Value", 300.0, is_header=True),
            _cell(1, 0, "Costs", 50.0),
            _cell(1, 1, "$40M", 300.0),
        ])

        merged = NativeTableExtractor.merge_cross_page_tables([[first], [second]])
        table = merged[0][0]
        assert table.rows == 3
        assert sum(1 for c in table.cells if c.text == "Metric") == 1

    def test_mismatched_columns_are_not_merged(self):
        first = _table("t1", page=1, rows=1, cells=[_cell(0, 0, "A", 50.0)])
        second = _table("t2", page=2, rows=1, cells=[_cell(0, 0, "B", 50.0)])
        second.columns = 5

        merged = NativeTableExtractor.merge_cross_page_tables([[first], [second]])
        assert len(merged[0]) == 1
        assert len(merged[1]) == 1

    def test_misaligned_columns_are_not_merged(self):
        first = _table("t1", page=1, rows=1, cells=[
            _cell(0, 0, "A", 50.0), _cell(0, 1, "B", 300.0),
        ])
        second = _table("t2", page=2, rows=1, cells=[
            _cell(0, 0, "C", 200.0), _cell(0, 1, "D", 450.0),
        ])
        merged = NativeTableExtractor.merge_cross_page_tables([[first], [second]])
        assert len(merged[1]) == 1

    def test_intervening_page_without_tables_breaks_continuation(self):
        first = _table("t1", page=1, rows=1, cells=[_cell(0, 0, "A", 50.0)])
        third = _table("t3", page=3, rows=1, cells=[_cell(0, 0, "B", 50.0)])
        merged = NativeTableExtractor.merge_cross_page_tables([[first], [], [third]])
        assert len(merged[0]) == 1
        assert len(merged[2]) == 1


class TestSpacerColumns:
    """Narrow spacer columns must not be absorbed as spans.

    Financial tables commonly place a few-point spacer between value columns.
    A midpoint-based span test swallows them, which silently drops real cells.
    """

    def _grid(self):
        # Three content columns separated by ~4pt spacers, two ordinary rows.
        edges = [(36.0, 196.0), (196.0, 200.0), (200.0, 263.0), (263.0, 267.0)]
        grid = []
        for r in range(2):
            y0, y1 = 100.0 + r * 20, 120.0 + r * 20
            grid.append([
                BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1) for x0, x1 in edges
            ])
        return grid

    def test_adjacent_narrow_column_is_not_absorbed(self):
        grid = self._grid()
        spans = NativeTableExtractor._compute_spans(grid, num_rows=2, num_cols=4)
        assert all(spans[(r, c)] == (1, 1) for r in range(2) for c in range(4)), spans

    def test_no_cells_are_dropped_for_a_dense_grid(self):
        grid = self._grid()
        spans = NativeTableExtractor._compute_spans(grid, num_rows=2, num_cols=4)
        consumed = [k for k, v in spans.items() if v == (0, 0)]
        assert consumed == []

    def test_a_genuine_span_is_still_detected(self):
        wide = BoundingBox(x0=36.0, y0=100.0, x1=267.0, y1=120.0)
        row1 = [
            BoundingBox(x0=x0, y0=120.0, x1=x1, y1=140.0)
            for x0, x1 in [(36.0, 196.0), (196.0, 200.0), (200.0, 263.0), (263.0, 267.0)]
        ]
        grid = [[wide, None, None, None], row1]
        spans = NativeTableExtractor._compute_spans(grid, num_rows=2, num_cols=4)
        assert spans[(0, 0)] == (1, 4)
        assert spans[(0, 1)] == (0, 0)
        assert all(spans[(1, c)] == (1, 1) for c in range(4))
