from pathlib import Path

import pytest

from trarium import youtube


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=5XLEjBlSaAY",
    "https://youtu.be/5XLEjBlSaAY?t=3",
    "https://www.youtube.com/shorts/5XLEjBlSaAY",
    "5XLEjBlSaAY",
])
def test_video_id(url):
    assert youtube.video_id(url) == "5XLEjBlSaAY"


def test_bad_url():
    with pytest.raises(youtube.YouTubeError):
        youtube.video_id("https://example.com/")


def test_falls_back_to_transcript_api(tmp_path, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("Sign in to confirm you're not a bot")

    def fake(vid, out, langs):
        path = Path(out) / f"{vid}.en.srt"
        path.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
        return path, {"method": "youtube-transcript-api"}

    monkeypatch.setattr(youtube, "_with_yt_dlp", broken)
    monkeypatch.setattr(youtube, "_with_transcript_api", fake)
    path, meta = youtube.fetch_subtitles("https://youtu.be/5XLEjBlSaAY", tmp_path)
    assert path.exists() and meta["method"] == "youtube-transcript-api"


def test_block_hint(tmp_path, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(youtube, "_with_yt_dlp", broken)
    monkeypatch.setattr(youtube, "_with_transcript_api", broken)
    with pytest.raises(youtube.YouTubeError, match="TRARIUM_YT_PROXY"):
        youtube.fetch_subtitles("5XLEjBlSaAY", tmp_path)


AUTO_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.330 align:start position:0%
 
hello<00:00:00.480><c> everyone</c><00:00:00.900><c> and</c>

00:00:02.330 --> 00:00:02.340 align:start position:0%
hello everyone and
 

00:00:02.340 --> 00:00:04.500 align:start position:0%
hello everyone and
welcome<00:00:02.800><c> to</c><00:00:03.100><c> the</c><00:00:03.400><c> show</c>

00:00:04.500 --> 00:00:04.510 align:start position:0%
welcome to the show
 
"""


def test_auto_vtt_has_no_rolling_duplicates():
    srt = youtube.vtt_to_srt(AUTO_VTT)
    texts = [l for l in srt.splitlines() if l and not l.isdigit() and "-->" not in l]
    assert texts == ["hello everyone and", "welcome to the show"]
    assert "00:00:00,000 --> 00:00:02,340" in srt
    assert "<c>" not in srt


def test_manual_vtt_kept():
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nHi &amp; bye\n\n00:00:03.000 --> 00:00:04.000\n<i>Next</i>\n"
    srt = youtube.vtt_to_srt(vtt)
    assert "Hi & bye" in srt and "Next" in srt and "00:00:01,000 --> 00:00:02,500" in srt


def test_pick_language_falls_back_to_original():
    info = {"language": "de", "subtitles": {}, "automatic_captions": {"de-orig": [], "de": [], "fa": [], "en": []}}
    assert youtube._pick_language(info, ["en-GB"]) == ["de-orig"] or youtube._pick_language(info, ["en-GB"]) == ["de"]
    assert youtube._pick_language(info, ["en"]) == ["en"]
    assert youtube._pick_language({"subtitles": {}, "automatic_captions": {}}, ["en"]) == []


def test_yt_dlp_path_returns_clean_srt(tmp_path, monkeypatch):
    import sys, types

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download):
            info = {"id": "5XLEjBlSaAY", "title": "t", "language": "en",
                    "subtitles": {}, "automatic_captions": {"en": [], "en-orig": []}}
            if download:
                assert self.opts["subtitleslangs"] == ["en", "en-orig"]
                assert self.opts.get("cookiefile") == "c.txt"
                (tmp_path / "5XLEjBlSaAY.en.vtt").write_text(AUTO_VTT, encoding="utf-8")
            return info

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    path, meta = youtube.fetch_subtitles("https://youtu.be/5XLEjBlSaAY", tmp_path, cookies="c.txt")
    assert path.suffix == ".srt" and meta["generated"] and meta["language"] == "en"
    assert path.read_text(encoding="utf-8").count("hello everyone and") == 1
