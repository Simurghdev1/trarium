import zipfile

from trarium.output import save_as_epub
from trarium.processors.pipeline import TranslationResult


def _result():
    text = "\n---\n".join(f"فصل {i}\n" + "\n\n".join("این یک پاراگراف آزمایشی برای ترجمه است. " * 8 for _ in range(40))
                         for i in range(1, 6))
    return TranslationResult(title="آزمون", author="", original_lang="en", target_lang="fa",
                             translated_text=text, total_chunks=1, translated_chunks=1, failed_chunks=0)


def test_compressed_epub_is_smaller_and_valid(tmp_path):
    big = save_as_epub(_result(), str(tmp_path / "a.epub"), compress=False)
    small = save_as_epub(_result(), str(tmp_path / "b.epub"))
    assert (tmp_path / "b.epub").stat().st_size < (tmp_path / "a.epub").stat().st_size / 2
    with zipfile.ZipFile(small) as zf:
        first = zf.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/epub+zip"
        assert all(i.compress_type == zipfile.ZIP_DEFLATED for i in zf.infolist()[1:])
        assert zf.testzip() is None
    with zipfile.ZipFile(big) as zf:
        assert all(i.compress_type == zipfile.ZIP_STORED for i in zf.infolist())
