"""تست‌های بخش‌های خالص دوبله و زیرنویس‌ساز (بدون شبکه)."""
import numpy as np
import pytest

from trarium.media import langs
from trarium.media.ass import AssStyle, build_ass
from trarium.media.gemini_client import GeminiClient, QuotaExhausted, classify_429
from trarium.media.live import is_quota_error, place_live_chunks, setup_message
from trarium.media.mix import duck_envelope, mix, place_clips
from trarium.media.timeline import (RLM, Segment, atempo_chain, fmt_time, normalize, parse_time,
                                    plan_placements, split_long, to_srt, to_vtt, wrap_lines)
from trarium.media.transcribe import parse_gemini_segments
from trarium.media.translate import build_batches, parse_numbered, translate_segments


def test_parse_and_format_time():
    assert parse_time(12.5) == 12.5
    assert parse_time("01:02.300") == pytest.approx(62.3)
    assert parse_time("00:01:02,300") == pytest.approx(62.3)
    assert fmt_time(3723.456) == "01:02:03,456"
    assert fmt_time(1.5, ".") == "00:00:01.500"
    with pytest.raises(ValueError):
        parse_time("abc")


def test_normalize_removes_overlap_and_empty():
    segs = [Segment(2, 3, "b"), Segment(0, 2.5, "a"), Segment(5, 5, "  "), Segment(9, 12, "c")]
    out = normalize(segs, total=10)
    assert [s.text for s in out] == ["a", "b", "c"]
    assert out[1].start >= out[0].end
    assert out[-1].end <= 10


def test_split_long_keeps_time_range():
    seg = Segment(10, 22, "First sentence is here. Second sentence follows, and then a third one that is long enough.")
    out = split_long([seg], max_chars=40, max_seconds=5)
    assert len(out) >= 2
    assert out[0].start == 10 and out[-1].end == 22
    assert all(a.end == pytest.approx(b.start) for a, b in zip(out, out[1:]))
    assert " ".join(s.text for s in out).split() == seg.text.split()


def test_wrap_lines_balanced_two_lines():
    text = "She describes fascia not as the organ of form,"
    lines = wrap_lines(text, 42).split("\n")
    assert len(lines) == 2
    assert min(len(x) for x in lines) > 10   # no orphan word
    assert wrap_lines("short", 42) == "short"


def test_srt_vtt_modes_and_rtl_marks():
    segs = [Segment(0.5, 2, "Hello world", "سلام دنیا")]
    srt = to_srt(segs, "translation", rtl=True)
    assert "00:00:00,500 --> 00:00:02,000" in srt
    assert f"{RLM}سلام دنیا{RLM}" in srt
    bi = to_srt(segs, "bilingual", rtl=True)
    assert "Hello world" in bi and "سلام دنیا" in bi
    assert to_vtt(segs, "original").startswith("WEBVTT")
    assert "00:00:00.500 --> 00:00:02.000\nHello world" in to_vtt(segs, "original")


def test_plan_placements_speeds_up_and_never_overlaps():
    segs = [Segment(0, 2, "a"), Segment(2, 4, "b"), Segment(10, 12, "c")]
    plans = plan_placements(segs, [3.0, 1.0, 1.0], max_tempo=1.35)
    assert plans[0].tempo == pytest.approx(1.35)
    # first clip still too long for its slot → second starts after it
    assert plans[1].at >= plans[0].at + plans[0].length
    assert plans[2].tempo == 1.0 and plans[2].at == 10


def test_atempo_chain_limits():
    assert atempo_chain(1.2) == "atempo=1.2000"
    assert atempo_chain(3.0).startswith("atempo=2.0,")
    assert atempo_chain(0.3).startswith("atempo=0.5,")


def test_place_clips_and_mix_modes():
    rate = 1000
    track = place_clips([np.ones(100, np.float32) * 0.5], [1.0], 2.0, rate)
    assert len(track) >= 2000 and track[1050] == pytest.approx(0.5) and track[500] == 0
    original = np.ones(2001, np.float32) * 0.4
    ducked = mix(original, track, rate, mode="duck", bed=0.2)
    assert ducked[1050] < 0.5 + 0.4 * 0.5    # original lowered under the voice
    assert ducked[1900] == pytest.approx(0.4, abs=0.02)  # untouched elsewhere
    assert np.allclose(mix(original, track, rate, mode="replace")[:900], 0)
    env = duck_envelope(np.zeros(100, bool), rate)
    assert np.allclose(env, 1.0)


def test_parse_gemini_segments_accepts_fence_and_mmss():
    raw = '```json\n{"language":"en","segments":[{"start":"00:01.0","end":"00:03.5","text":" Hi  there "},' \
          '{"start":4,"end":3,"text":"x"}]}\n```'
    lang, segs = parse_gemini_segments(raw, total=10)
    assert lang == "en" and segs[0].text == "Hi there" and segs[0].start == 1.0
    assert segs[1].end > segs[1].start


def test_translate_batches_and_numbering():
    segs = [Segment(i, i + 1, f"line {i}") for i in range(5)]
    assert build_batches(segs, max_chars=30) == [[0, 1], [2, 3], [4]]
    assert parse_numbered("[1] الف\n[2] ب", [1, 2]) == {1: "الف", 2: "ب"}
    assert parse_numbered("[۱] الف\n[۲] ب", [1, 2]) == {1: "الف", 2: "ب"}   # Persian digits
    assert parse_numbered("[1] الف", [1, 2]) is None

    class Broken:
        def __init__(self):
            self.calls = 0

        def translate(self, text, lang, instructions=""):
            self.calls += 1
            return "garbage" if "[1]" in text else f"T:{text}"

    tr = Broken()
    stats = translate_segments(segs[:2], tr, "fa")
    assert [s.translation for s in segs[:2]] == ["T:line 0", "T:line 1"]
    assert stats["fallback_lines"] == 2 and tr.calls == 3


def test_build_ass_rtl_and_style():
    segs = [Segment(1, 2.5, "Hi", "سلام، دنیا")]
    ass = build_ass(segs, 1920, 1080, AssStyle(font="Vazirmatn", size_pct=6, position="top"), "bilingual", rtl=True)
    assert "PlayResY: 1080" in ass
    assert "Style: Main,Vazirmatn,65," in ass
    assert ",8," in ass.split("Style: Main")[1].split("\n")[0]   # top alignment
    assert f"{RLM}سلام، دنیا{RLM}" in ass and "{\\rOrig}Hi" in ass
    assert "Dialogue: 0,0:00:01.00,0:00:02.50,Main" in ass


def test_langs_resolve_and_voices():
    assert langs.resolve("فارسی").code == "fa"
    assert langs.resolve("Persian").code == "fa"
    assert langs.edge_voice("fa", "male") == "fa-IR-FaridNeural"
    assert langs.is_rtl("ar") and not langs.is_rtl("en")
    free = langs.resolve("sw")
    assert free.code == "sw" and not free.rtl


def test_quota_classification_and_key_rotation(monkeypatch):
    assert classify_429('{"retryDelay": "21s", "quotaId": "GenerateRequestsPerMinutePerProject"}') == ("minute", 21.0)
    assert classify_429('{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}')[0] == "exhausted"
    assert is_quota_error("RESOURCE_EXHAUSTED") and not is_quota_error("bad request")

    used = []

    class Resp:
        def __init__(self, code, body="{}"):
            self.status_code, self.text, self.headers = code, body, {}

        def json(self):
            return {"ok": True}

    def fake(method, url, headers=None, **kw):
        used.append(headers["x-goog-api-key"])
        return Resp(429, '{"quotaId": "PerDay"}') if headers["x-goog-api-key"] == "k1" else Resp(200)

    monkeypatch.setattr("trarium.media.gemini_client.requests.request", fake)
    client = GeminiClient(["k1", "k2"])
    assert client.request("POST", "/x") == {"ok": True}
    assert used == ["k1", "k2"] and client.index == 1
    with pytest.raises(QuotaExhausted):
        GeminiClient(["k1"]).request("POST", "/x")


def test_live_setup_and_chunk_placement():
    msg = setup_message("gemini-3.5-live-translate-preview", "fa", "Persian")
    cfg = msg["setup"]["generationConfig"]
    assert cfg["translationConfig"]["targetLanguageCode"] == "fa"
    assert "systemInstruction" not in msg["setup"]
    fb = setup_message("gemini-2.5-flash-native-audio-latest", "fa", "Persian")
    assert "Persian" in fb["setup"]["systemInstruction"]["parts"][0]["text"]
    rate = 1000
    chunks = [(3.0, np.zeros(500)), (3.1, np.zeros(500)), (8.0, np.zeros(1000))]
    placed = place_live_chunks(chunks, lag=2.0, rate=rate)
    assert placed[0][0] == pytest.approx(1.0)
    assert placed[1][0] == pytest.approx(1.5)          # contiguous with previous chunk
    assert placed[2][0] == pytest.approx(6.0)          # new utterance after a gap
