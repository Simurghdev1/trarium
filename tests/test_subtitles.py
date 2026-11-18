import time
from pathlib import Path

from trarium.subtitles import read_subtitles

SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nHello there.\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nGeneral <i>Kenobi</i>.\n"
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_srt_keeps_numbers_and_timing(tmp_path):
    document = read_subtitles(_write(tmp_path, "movie.srt", SRT))
    assert document is not None
    assert [cue.text for cue in document.cues] == ["Hello there.", "General Kenobi."]
    rendered = document.render(["سلام.", "ژنرال کنوبی."])
    assert "1\n00:00:01,000 --> 00:00:03,000\nسلام." in rendered
    assert "2\n00:00:04,000 --> 00:00:06,000\nژنرال کنوبی." in rendered
    assert "Hello" not in rendered


def test_vtt_header_and_cue_ids(tmp_path):
    text = "WEBVTT\n\ncue-1\n00:00:01.000 --> 00:00:02.000\nFirst line\n"
    document = read_subtitles(_write(tmp_path, "movie.vtt", text))
    rendered = document.render(["خط اول"])
    assert rendered.startswith("WEBVTT")
    assert "cue-1" in rendered
    assert "00:00:01.000 --> 00:00:02.000" in rendered
    assert "خط اول" in rendered


def test_ass_dialogue_line_keeps_its_fields(tmp_path):
    line = (
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\i1}Hello there{\\i0}\\NSecond line\n"
    )
    document = read_subtitles(_write(tmp_path, "movie.ass", line))
    assert document is not None
    rendered = document.render(["سلام\nخط دوم"])
    assert "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,سلام\\Nخط دوم" in rendered
    assert "[Events]" in rendered


def test_microdvd_timing_preserved(tmp_path):
    document = read_subtitles(_write(tmp_path, "movie.sub", "{100}{200}Hello|world\n"))
    assert document is not None
    rendered = document.render(["سلام|دنیا"])
    assert "{100}{200}سلام|دنیا" in rendered


def test_lrc_timestamps_preserved(tmp_path):
    document = read_subtitles(_write(tmp_path, "song.lrc", "[00:12.30]Sing a song\n[00:15.00]Second line\n"))
    assert document is not None
    rendered = document.render(["آواز بخوان", "خط دوم"])
    assert "[00:12.30]آواز بخوان" in rendered
    assert "[00:15.00]خط دوم" in rendered


class _FakeTranslator:
    provider = "fake"
    model = "fake-1"
    model_name = "fake-1"
    last_usage = {"input_tokens": 5, "output_tokens": 5}

    def get_token_limit(self):
        return 8000

    def translate(self, text, target_lang="fa"):
        return "\n".join("TR: " + line if line.strip() else line for line in str(text).splitlines())


def test_subtitle_job_writes_a_usable_srt(tmp_path, monkeypatch):
    from trarium.jobs.manager import JobManager
    from trarium.jobs.store import JobStore

    monkeypatch.setattr("trarium.failover.build_chain", lambda *a, **k: _FakeTranslator())
    store = JobStore()
    manager = JobManager(store=store, recover=False)
    manager.set_max_concurrent(1)
    job_id = manager.submit("book", "subtitle", config={"workers": 1, "output_format": "pdf"},
                            source_bytes=SRT.encode("utf-8"), source_name="movie.srt")
    deadline = time.time() + 20
    while time.time() < deadline and store.get(job_id)["status"] not in {"completed", "failed"}:
        time.sleep(0.05)
    job = store.get(job_id)
    manager.shutdown()
    assert job["status"] == "completed", job.get("error")
    result = Path(job["result_path"])
    assert result.suffix == ".srt", result
    content = result.read_text(encoding="utf-8")
    assert "00:00:01,000 --> 00:00:03,000" in content
    assert "TR: Hello there." in content
    assert "TR: General Kenobi." in content
