import subprocess
from pathlib import Path

import numpy as np
import pytest

from trarium.media import ffmpeg_tools as ff
from trarium.media import pipelines
from trarium.media.mix import mix, trim_pauses

RATE = 24000


def test_trim_pauses_shortens_long_silence():
    tone = np.sin(np.linspace(0, 400, RATE // 2)).astype(np.float32) * 0.5
    clip = np.concatenate([tone, np.zeros(RATE, np.float32), tone])
    out = trim_pauses(clip, RATE)
    assert len(out) < len(clip) - RATE // 2
    assert len(out) >= 2 * len(tone)


def test_mix_novoice_keeps_background_under_dub():
    bg = np.full(RATE * 2, 0.2, np.float32)
    dub = np.zeros(RATE * 2, np.float32)
    dub[RATE:] = 0.5
    out = mix(bg, dub, RATE, mode="novoice")
    assert out[RATE // 4] > 0  # موزیک قبل از دوبله می‌ماند


def test_original_track_novoice_without_demucs_falls_back(monkeypatch):
    from trarium.media import separate
    monkeypatch.setattr(separate, "available", lambda: False)
    logs = []
    track, mode = pipelines._original_track(Path("x.mp4"), "novoice", RATE, logs.append)
    assert track is None and mode == "replace" and logs


@pytest.mark.skipif(not ff.shutil.which("ffmpeg"), reason="ffmpeg")
def test_extract_embedded_subtitles(tmp_path):
    srt = tmp_path / "in.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:01,500\nHello there\n\n2\n00:00:01,600 --> 00:00:02,500\nBye\n",
                   encoding="utf-8")
    mkv = tmp_path / "v.mkv"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=black:s=64x64:d=3",
                    "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-i", str(srt), "-t", "3",
                    "-map", "0:v", "-map", "1:a", "-map", "2:s", "-c:s", "srt",
                    "-metadata:s:s:0", "language=eng", str(mkv)], check=True)

    class T:
        def translate_batch(self, texts, *a, **k):
            return ["ت " + t for t in texts]

        def translate(self, text, *a, **k):
            return "ت " + text

    res = pipelines.extract_subtitles(mkv, tmp_path / "w", stem="v")
    assert res.stats["extracted"] == 1
    assert "Hello there" in res.files["srt_0"].read_text(encoding="utf-8")
