from __future__ import annotations

from trueparse.chunking.chunker import DocumentChunker
from trueparse.config.options import ParseOptions
from trueparse.contracts.document import RepresentationReport
from trueparse.contracts.response import ParsedOutputData
from trueparse.exceptions import PDFEngineError
from trueparse.exporters.html import HTMLExporter
from trueparse.exporters.json import DocumentProjector
from trueparse.exporters.markdown import MarkdownExporter
from trueparse.exporters.tables import TableRenderer
from trueparse.exporters.text import TextExporter
from trueparse.models.document import Document
from trueparse.models.elements import TableElement
from trueparse.models.enums import VISUAL_TYPES, ElementType, ErrorCode, ImageInclusionMode, OutputFormat
from trueparse.runtime.timing import StageTimings


class OutputRenderer:
    def __init__(self, options: ParseOptions, timings: StageTimings):
        self.options = options
        self.timings = timings
        self.reports: dict[str, RepresentationReport] = {}

    def _timed(self, stage, function, *args, **kwargs):
        with self.timings.measure(stage):
            return function(*args, **kwargs)

    def render(self, doc: Document) -> ParsedOutputData:
        elements = [element for page in doc.pages for element in page.elements]
        if len(elements) > self.options.limits.max_nodes:
            raise PDFEngineError(ErrorCode.PDF_RESOURCE_LIMIT, "Extracted content exceeds max_nodes.")
        if (
            sum(len(getattr(element, "cells", ())) for element in elements)
            > self.options.limits.max_table_cells
        ):
            raise PDFEngineError(ErrorCode.PDF_RESOURCE_LIMIT, "Extracted tables exceed max_table_cells.")
        if (
            sum(element.rows * element.columns for element in elements if isinstance(element, TableElement))
            > self.options.limits.max_table_cells
        ):
            raise PDFEngineError(ErrorCode.PDF_RESOURCE_LIMIT, "Table grids exceed max_table_cells.")
        formats = self.options.output_config.formats
        j_opts = self.options.output_config.json_options
        output_data = ParsedOutputData()
        markdown_tables = (
            OutputFormat.MARKDOWN in formats
            or OutputFormat.CHUNKS in formats
            or (OutputFormat.JSON in formats and j_opts.tables.markdown)
        )
        html_tables = OutputFormat.HTML in formats or (OutputFormat.JSON in formats and j_opts.tables.html)
        if markdown_tables or html_tables:
            for page in doc.pages:
                for element in page.elements:
                    if element.type == ElementType.TABLE:
                        self._timed(
                            "table_rendering",
                            TableRenderer.ensure_representations,
                            element,
                            markdown=markdown_tables,
                            html=html_tables,
                        )

        if OutputFormat.JSON in formats:
            output_data.document = self._timed("export_json", DocumentProjector.project, doc, j_opts)
        if OutputFormat.MARKDOWN in formats:
            output_data.markdown = self._timed("export_markdown", MarkdownExporter.export, doc)
        if OutputFormat.HTML in formats:
            output_data.html = self._timed("export_html", HTMLExporter.export, doc)
        if OutputFormat.TEXT in formats:
            output_data.text = self._timed("export_text", TextExporter.export, doc)
        if OutputFormat.CHUNKS in formats:
            chunks = self._timed(
                "chunking",
                DocumentChunker.chunk,
                doc,
                strategy=self.options.chunk_strategy,
                max_tokens=self.options.chunk_max_tokens,
                overlap_tokens=self.options.chunk_overlap_tokens,
                tokenizer=self.options.chunk_tokenizer,
            )
            output_data.chunks = [chunk.model_dump(mode="json") for chunk in chunks]
            if any(chunk.budget_exceeded for chunk in chunks):
                warning = "Some atomic table rows or Unicode characters exceed the chunk budget; inspect budget_exceeded on chunks."
                if warning not in doc.warnings:
                    doc.warnings.append(warning)

        node_ids = [element.id for page in doc.pages for element in page.elements]
        visual_ids = {e.id for p in doc.pages for e in p.elements if e.type in VISUAL_TYPES}
        for fmt in formats:
            included = set(node_ids)
            losses = []
            if fmt != OutputFormat.JSON and any(e.metadata.get("parent_table_id") for e in elements):
                losses.append("Visuals inside tables are exported as separate source-linked occurrences.")
            if fmt == OutputFormat.JSON:
                included = set((output_data.document or {}).get("nodes", {}))
            elif fmt == OutputFormat.CHUNKS:
                included = {key for chunk in output_data.chunks or [] for key in chunk["element_ids"]}
                if visual_ids:
                    losses.append("Chunks contain text; visual bytes are not embedded in chunk text.")
            elif fmt == OutputFormat.TEXT and visual_ids:
                losses.append("Visual content is represented by source markers, not image bytes.")
            elif visual_ids and self.options.output_config.json_options.images == ImageInclusionMode.NONE:
                losses.append("Visual bytes were omitted by request; source markers are preserved.")
            elif visual_ids and any(
                not getattr(e, "asset_path", None) for e in elements if e.type in VISUAL_TYPES
            ):
                losses.append("Some visual bytes are unavailable; source markers are preserved.")
            self.reports[fmt.value] = RepresentationReport(
                rendered_node_ids=[key for key in node_ids if key in included],
                excluded_node_ids=[key for key in node_ids if key not in included],
                losses=losses,
            )
        return output_data
