import pytest

from trueparse.core.enums import ErrorCode
from trueparse.core.errors import PDFEngineError
from trueparse.core.security import contain_path, sanitize_identifier
from trueparse.storage.filesystem import FileSystemStorage


class TestSanitizeIdentifier:
    def test_keeps_safe_characters(self):
        assert sanitize_identifier("doc_abc-123") == "doc_abc-123"

    @pytest.mark.parametrize(
        "hostile",
        ["../../etc/passwd", "..\\..\\windows", "doc/../../x", "C:\\Windows\\System32"],
    )
    def test_strips_path_separators(self, hostile):
        cleaned = sanitize_identifier(hostile)
        assert "/" not in cleaned
        assert "\\" not in cleaned
        assert "." not in cleaned
        assert ":" not in cleaned

    def test_empty_input_uses_fallback(self):
        assert sanitize_identifier("///", fallback="doc_default") == "doc_default"


class TestContainPath:
    def test_allows_child_paths(self, tmp_path):
        result = contain_path("nested/file.txt", tmp_path)
        assert result == (tmp_path / "nested" / "file.txt").resolve()

    def test_allows_the_root_itself(self, tmp_path):
        assert contain_path(tmp_path, tmp_path) == tmp_path.resolve()

    def test_rejects_parent_traversal(self, tmp_path):
        with pytest.raises(PDFEngineError) as exc:
            contain_path("../escaped.txt", tmp_path)
        assert exc.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_rejects_absolute_path_outside_root(self, tmp_path):
        outside = tmp_path.parent / "sibling"
        with pytest.raises(PDFEngineError):
            contain_path(outside, tmp_path)


class TestStorageContainment:
    def test_document_dir_stays_inside_root(self, tmp_path):
        storage = FileSystemStorage(root_path=tmp_path)
        doc_dir = storage.get_document_dir("../../escape_attempt")
        assert tmp_path.resolve() in doc_dir.parents

    def test_source_filename_directory_component_is_stripped(self, tmp_path, sample_pdf_path):
        storage = FileSystemStorage(root_path=tmp_path)
        saved = storage.save_source_pdf(
            source_path=sample_pdf_path,
            document_id="doc_test",
            original_filename="../../../evil.pdf",
        )
        assert saved.name == "evil.pdf"
        assert tmp_path.resolve() in saved.parents

    def test_save_text_is_atomic_and_contained(self, tmp_path):
        storage = FileSystemStorage(root_path=tmp_path)
        target_dir = tmp_path / "out"
        written = storage.save_text("hello", target_dir, "../escape.txt")
        assert written.name == "escape.txt"
        assert written.parent == target_dir.resolve()
        assert written.read_text(encoding="utf-8") == "hello"
        # The temporary file used for the atomic swap must not survive.
        assert list(target_dir.iterdir()) == [written]

    def test_save_text_overwrites_cleanly(self, tmp_path):
        storage = FileSystemStorage(root_path=tmp_path)
        target_dir = tmp_path / "out"
        storage.save_text("first", target_dir, "doc.json")
        second = storage.save_text("second", target_dir, "doc.json")
        assert second.read_text(encoding="utf-8") == "second"
        assert list(target_dir.iterdir()) == [second]
