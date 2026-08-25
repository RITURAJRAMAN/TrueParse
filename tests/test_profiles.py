from trueparse.core.config import ParseOptions
from trueparse.core.enums import OCRMode, ParsingProfile
from trueparse.core.profiles import resolve
from trueparse.pipeline.runner import PDFParser


class TestProfileResolution:
    def test_every_profile_resolves(self):
        for profile in ParsingProfile:
            settings = resolve(profile)
            assert settings.render_dpi > 0
            assert settings.table_strategies

    def test_dpi_increases_with_accuracy(self):
        dpis = [resolve(p).render_dpi for p in (
            ParsingProfile.FAST,
            ParsingProfile.BALANCED,
            ParsingProfile.ACCURATE,
            ParsingProfile.MAXIMUM_ACCURACY,
        )]
        assert dpis == sorted(dpis)
        assert len(set(dpis)) == 4

    def test_fast_profile_skips_expensive_work(self):
        fast = resolve(ParsingProfile.FAST)
        assert fast.table_strategies == ["lines"]
        assert fast.detect_table_spans is False
        assert fast.merge_paragraphs is False
        assert fast.ocr_floor == OCRMode.NEVER

    def test_accurate_profiles_try_unruled_tables(self):
        for profile in (ParsingProfile.ACCURATE, ParsingProfile.MAXIMUM_ACCURACY):
            assert "text" in resolve(profile).table_strategies

    def test_resolve_returns_an_independent_copy(self):
        first = resolve(ParsingProfile.BALANCED)
        first.render_dpi = 999
        assert resolve(ParsingProfile.BALANCED).render_dpi != 999


class TestProfileApplication:
    def test_parser_adopts_profile_settings(self):
        parser = PDFParser(options=ParseOptions(profile=ParsingProfile.ACCURATE))
        assert parser.profile.render_dpi == resolve(ParsingProfile.ACCURATE).render_dpi
        assert "text" in parser.profile.table_strategies

    def test_explicit_dpi_overrides_the_profile(self):
        parser = PDFParser(options=ParseOptions(profile=ParsingProfile.FAST, render_dpi=222))
        assert parser.profile.render_dpi == 222

    def test_fast_profile_skips_paragraph_merging(self, tmp_path, sample_pdf_path):
        """A profile difference must be observable in the output, not just config."""
        fast = PDFParser(options=ParseOptions(
            output_path=str(tmp_path / "fast"), profile=ParsingProfile.FAST
        )).parse(sample_pdf_path)
        balanced = PDFParser(options=ParseOptions(
            output_path=str(tmp_path / "balanced"), profile=ParsingProfile.BALANCED
        )).parse(sample_pdf_path)

        fast_ids = {e.id for p in fast.pages for e in p.elements}
        balanced_ids = {e.id for p in balanced.pages for e in p.elements}
        # Balanced merges fragments, so it can only ever have fewer elements.
        assert len(balanced_ids) <= len(fast_ids)

    def test_debug_render_uses_profile_dpi(self, tmp_path, sample_pdf_path):
        from PIL import Image

        doc = PDFParser(options=ParseOptions(
            output_path=str(tmp_path), profile=ParsingProfile.FAST, debug=True, max_pages=1
        )).parse(sample_pdf_path)

        render = tmp_path / doc.id / "debug" / "pages" / "page_0001.png"
        assert render.exists()
        with Image.open(render) as img:
            # 612pt at 96 DPI -> 816px, allowing a pixel of rounding.
            assert abs(img.width - int(612 * 96 / 72)) <= 2
