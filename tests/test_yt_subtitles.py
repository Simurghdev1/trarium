"""YouTube subtitle link handling and timing-preserving translation."""
import tempfile
import unittest
from pathlib import Path

from trarium import yt_subtitles as yt
from trarium.subtitles import read_subtitles

SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nHello there.\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nGeneral Kenobi.\n\n"
    "3\n00:00:07,000 --> 00:00:09,000\nYou are a bold one.\n"
)


class ParseTests(unittest.TestCase):
    def test_watch_url(self):
        self.assertEqual(yt.parse_video_id("https://www.youtube.com/watch?v=5XLEjBlSaAY"),
                         "5XLEjBlSaAY")

    def test_short_and_embed_urls(self):
        self.assertEqual(yt.parse_video_id("https://youtu.be/5XLEjBlSaAY"), "5XLEjBlSaAY")
        self.assertEqual(yt.parse_video_id("https://www.youtube.com/shorts/5XLEjBlSaAY"),
                         "5XLEjBlSaAY")
        self.assertEqual(yt.parse_video_id("https://www.youtube.com/embed/5XLEjBlSaAY?rel=0"),
                         "5XLEjBlSaAY")

    def test_bare_id(self):
        self.assertEqual(yt.parse_video_id("5XLEjBlSaAY"), "5XLEjBlSaAY")

    def test_invalid_link_is_rejected(self):
        with self.assertRaises(yt.YouTubeSubtitleError):
            yt.parse_video_id("https://vimeo.com/12345")
        with self.assertRaises(yt.YouTubeSubtitleError):
            yt.parse_video_id("")


class TrackSelectionTests(unittest.TestCase):
    def test_preferred_manual_language_wins(self):
        manual = {"fa": [{}], "en": [{}]}
        automatic = {"en": [{}], "de": [{}]}
        self.assertEqual(yt.choose_track(manual, automatic, ["en"]), ("en", "manual"))

    def test_prefix_matches_regional_code(self):
        manual = {"en-US": [{}]}
        self.assertEqual(yt.choose_track(manual, {}, ["en"]), ("en-US", "manual"))

    def test_automatic_used_when_no_manual(self):
        self.assertEqual(yt.choose_track({}, {"en": [{}]}, ["en"]), ("en", "automatic"))

    def test_any_track_when_language_not_found(self):
        self.assertEqual(yt.choose_track({}, {"ja": [{}]}, ["en"]), ("ja", "automatic"))

    def test_no_subtitles_is_reported(self):
        with self.assertRaises(yt.YouTubeSubtitleError):
            yt.choose_track({}, {}, ["en"])

    def test_caption_file_prefers_vtt_over_json3(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "abc.json3").write_text("{}")
            vtt = folder / "abc.en.vtt"
            vtt.write_text("WEBVTT")
            self.assertEqual(yt._find_caption_file(folder, "abc"), vtt)


class TranslationTests(unittest.TestCase):
    def _document(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "movie.srt"
            path.write_text(SRT, encoding="utf-8")
            return read_subtitles(path)

    def test_every_cue_is_translated_and_timed(self):
        document = self._document()

        def translate(text):
            parts = [p for p in text.split("\n\n") if p.strip()]
            return "\n\n".join(f"ترجمه {index + 1}" for index, _ in enumerate(parts))

        texts = yt.translate_subtitle_document(document, translate)
        self.assertEqual(len(texts), 3)
        self.assertTrue(all(text.startswith("ترجمه") for text in texts))
        rendered = document.render(texts)
        self.assertIn("00:00:01,000 --> 00:00:03,000\nترجمه 1", rendered)
        self.assertIn("00:00:07,000 --> 00:00:09,000\nترجمه 3", rendered)
        self.assertNotIn("Hello there", rendered)

    def test_mismatched_boundaries_fall_back_to_single_cues(self):
        document = self._document()
        calls = []

        def translate(text):
            calls.append(text)
            return "یک خط ترجمه" if "\n\n" in text else "ترجمه تک‌خطی"

        texts = yt.translate_subtitle_document(document, translate)
        self.assertEqual(len(texts), 3)
        # One batched call followed by one call per cue.
        self.assertGreaterEqual(len(calls), 4)
        self.assertEqual(texts[0], "ترجمه تک‌خطی")

    def test_translate_file_keeps_the_format(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "in.srt"
            destination = Path(td) / "out.srt"
            source.write_text(SRT, encoding="utf-8")
            report = yt.translate_subtitle_file(source, destination, lambda text: "سلام")
            self.assertEqual(report["cues"], 3)
            self.assertEqual(report["translated"], 3)
            saved = read_subtitles(destination)
            self.assertEqual(len(saved.cues), 3)
            self.assertEqual(saved.cues[0].payload["timing"], "00:00:01,000 --> 00:00:03,000")


if __name__ == "__main__":
    unittest.main()
