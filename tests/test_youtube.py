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
