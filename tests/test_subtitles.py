"""Regression tests for subtitle translation.

The contract: a cue never loses its timing, a translation never swallows the
next line, and a model that answers badly cannot leave a line untranslated.
"""
from __future__ import annotations

from trarium.subtitles import (
    Cue,
    batches,
    fix_overlaps,
    merge_cues,
    parse_numbered,
    parse_segments,
    parse_srt,
    parse_vtt,
    to_bilingual,
    to_srt,
    to_vtt,
    translate_cues,
)

VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.920 --> 00:00:06.440
This is Fang Yuan, and in volume three,

00:00:06.440 --> 00:00:11.320
he's going to steal a dead hero's face.
"""

SRT = """1
00:00:01,000 --> 00:00:03,500
First line

2
00:00:04,000 --> 00:00:06,000
Second line
"""


class TestParsing:
    def test_vtt(self):
        cues = parse_vtt(VTT)
        assert len(cues) == 2
        assert cues[0].start == 0.92
        assert cues[0].end == 6.44
        assert cues[1].text.startswith("he's going")

    def test_srt(self):
        cues = parse_srt(SRT)
        assert [c.text for c in cues] == ["First line", "Second line"]
        assert cues[1].start == 4.0

    def test_segments_json_accepts_both_key_styles(self):
        cues = parse_segments([
            {"s": 1.5, "d": 2.0, "t": "one"},
            {"start": 5, "end": 7, "text": "two"},
            {"t": ""},
        ])
        assert [c.text for c in cues] == ["one", "two"]
        assert cues[1].end == 7.0


class TestWriting:
    def test_srt_roundtrip_keeps_timings(self):
        cues = [Cue(0.92, 6.44, "زیرنویس اول"), Cue(6.44, 11.32, "زیرنویس دوم")]
        text = to_srt(cues)
        assert "00:00:00,920 --> 00:00:06,440" in text
        again = parse_srt(text)
        assert [c.text for c in again] == ["زیرنویس اول", "زیرنویس دوم"]
        assert again[1].end == 11.32

    def test_vtt_uses_dots(self):
        text = to_vtt([Cue(1.5, 2.0, "x")])
        assert text.startswith("WEBVTT")
        assert "00:00:01.500 --> 00:00:02.000" in text

    def test_bilingual_keeps_both_languages(self):
        text = to_bilingual([Cue(0, 2, "Hello")], ["سلام"])
        assert "سلام" in text and "Hello" in text


class TestMerging:
    def test_short_fragments_are_joined(self):
        cues = [Cue(0, 1.4, "one"), Cue(1.4, 2.8, "two"), Cue(2.8, 4.0, "three")]
        merged = merge_cues(cues)
        assert len(merged) == 1
        assert merged[0].text == "one two three"
        assert merged[0].end == 4.0

    def test_a_long_silence_is_not_crossed(self):
        merged = merge_cues([Cue(0, 1, "one"), Cue(20, 21, "two")])
        assert len(merged) == 2

    def test_a_sentence_ending_stays_alone(self):
        merged = merge_cues([Cue(0, 1, "one."), Cue(1, 2, "two")])
        assert len(merged) == 2


class TestOverlaps:
    def test_a_cue_is_cut_before_the_next_one_starts(self):
        cues = fix_overlaps([Cue(0, 6.44, "a"), Cue(3.48, 8.6, "b")])
        assert cues[0].end <= cues[1].start
        assert cues[1].start == 3.48

    def test_a_short_cue_keeps_a_readable_length(self):
        cues = fix_overlaps([Cue(0, 5, "a"), Cue(0.05, 2, "b")])
        assert cues[0].end >= cues[0].start + 0.4


class TestNumberedAnswer:
    def test_parses_numbers(self):
        assert parse_numbered("1. سلام\n2) دنیا\n3- جهان", 3) == ["سلام", "دنیا", "جهان"]

    def test_missing_lines_stay_empty(self):
        parsed = parse_numbered("1. فقط خط اول", 3)
        assert parsed[0] == "فقط خط اول"
        assert parsed[1] is None and parsed[2] is None

    def test_batches_keep_the_order(self):
        cues = [Cue(i, i + 1, "x" * 60) for i in range(10)]
        groups = batches(cues, max_chars=400, max_lines=40)
        flat = [index for group in groups for index in group]
        assert flat == list(range(10))


class _BatchTranslator:
    """A model that answers only the lines it is asked for, in order."""

    provider = "stub"
    model = "stub"
    last_usage: dict = {}

    def __init__(self, drop_first: bool = False):
        self.drop_first = drop_first
        self.calls = 0

    def translate(self, text: str, target_lang: str = "fa") -> str:
        self.calls += 1
        if self.drop_first and self.calls == 1:
            return "1. ترجمهٔ ناقص"  # only one line out of the batch
        lines = [line for line in text.split("\n") if line[:1].isdigit()]
        return "\n".join(
            f"{line.split('.', 1)[0]}. ترجمهٔ {line.split('.', 1)[1].strip()[:20]}"
            for line in lines
        )


def test_every_cue_gets_a_translation():
    cues = [Cue(i, i + 2, f"line {i}") for i in range(6)]
    out = translate_cues(cues, _BatchTranslator())
    assert len(out) == 6
    assert all(text.strip() for text in out)
    assert all("ترجمهٔ" in text for text in out)


def test_a_short_answer_is_repaired_line_by_line():
    cues = [Cue(i, i + 2, f"line {i}") for i in range(4)]
    translator = _BatchTranslator(drop_first=True)
    out = translate_cues(cues, translator)
    assert all(text.strip() for text in out)
    assert translator.calls > 1
