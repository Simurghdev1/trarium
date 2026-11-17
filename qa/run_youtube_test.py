"""Translate the subtitles of a YouTube video with Trarium.

The video is not downloaded and nothing is re-uploaded: the transcript is read,
translated, and written back as subtitles that keep the original timings.

    GEMINI_API_KEY=... python qa/run_youtube_test.py \
        --url https://youtu.be/VIDEO --outdir qa/outputs/youtube-test

    # or, when YouTube blocks the machine that runs the job:
    python qa/run_youtube_test.py --segments segments.json --outdir ...
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trarium.subtitles import (  # noqa: E402
    merge_cues,
    parse_segments,
    parse_srt,
    parse_vtt,
    to_bilingual,
    to_srt,
    to_vtt,
    translate_cues,
)
from trarium.translators.fallback import FallbackTranslator  # noqa: E402
from trarium.translators.gemini import GeminiTranslator  # noqa: E402
from trarium.translators.openrouter import OpenRouterTranslator  # noqa: E402

GEMINI_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-flash-latest",
]
OPENROUTER_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "deepseek/deepseek-chat-v3.1:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]


def build_translator(providers: list[str]) -> FallbackTranslator:
    import os

    candidates, labels = [], []
    if "gemini" in providers and os.environ.get("GEMINI_API_KEY"):
        for model in GEMINI_MODELS:
            candidates.append(GeminiTranslator(api_key=os.environ["GEMINI_API_KEY"], model_name=model))
            labels.append(f"gemini/{model}")
    if "openrouter" in providers and os.environ.get("OPENROUTER_API_KEY"):
        for model in OPENROUTER_MODELS:
            candidates.append(OpenRouterTranslator(api_key=os.environ["OPENROUTER_API_KEY"], model_name=model))
            labels.append(f"openrouter/{model}")
    if not candidates:
        raise SystemExit("هیچ کلید API‌ای تنظیم نشده است.")
    return FallbackTranslator(candidates, labels)


def fetch_from_youtube(url: str, workdir: pathlib.Path) -> tuple[list, dict]:
    """Ask yt-dlp for the automatic captions. Returns ``(cues, metadata)``."""
    import yt_dlp

    outdir = workdir / "source"
    outdir.mkdir(parents=True, exist_ok=True)
    options = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-orig", "fa"],
        "subtitlesformat": "vtt",
        "outtmpl": str(outdir / "%(id)s.%(ext)s"),
        "quiet": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
    meta = {
        "id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "url": url,
    }
    files = sorted(outdir.glob("*.vtt"))
    if not files:
        raise SystemExit("زیرنویسی از یوتیوب دریافت نشد (احتمالاً یوتیوب این ماشین را مسدود کرده است).")
    cues = parse_vtt(files[0].read_text(encoding="utf-8", errors="ignore"))
    return cues, meta


def read_transcript(path: pathlib.Path) -> list:
    suffix = path.suffix.lower()
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".json":
        data = json.loads(raw)
        items = data.get("segments") if isinstance(data, dict) else data
        return parse_segments(items)
    if suffix in {".srt"}:
        return parse_srt(raw)
    return parse_vtt(raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=None, help="لینک یوتیوب")
    parser.add_argument("--segments", default=None, help="فایل زیرنویس یا JSON بخش‌ها")
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--providers", default="gemini,openrouter")
    parser.add_argument("--batch-chars", type=int, default=1400)
    parser.add_argument("--max-cue-chars", type=int, default=84)
    parser.add_argument("--style", default="subtitle")
    args = parser.parse_args()

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    meta: dict = {"url": args.url, "source": args.segments}

    started = time.time()
    if args.segments:
        raw_cues = read_transcript(pathlib.Path(args.segments))
        if pathlib.Path(args.segments).suffix.lower() == ".json":
            try:
                data = json.loads(pathlib.Path(args.segments).read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("title"):
                    meta["title"] = str(data["title"]).replace("Transcript of ", "").replace(" - YouTubeToTranscript.com", "")
            except Exception:
                pass
    elif args.url:
        raw_cues, meta = fetch_from_youtube(args.url, outdir)
    else:
        raise SystemExit("یا --url بدهید یا --segments")

    if not raw_cues:
        raise SystemExit("زیرنویسی پیدا نشد.")

    cues = merge_cues(raw_cues, max_chars=args.max_cue_chars)
    (outdir / "01_original.en.srt").write_text(to_srt(cues), encoding="utf-8")

    translator = build_translator(args.providers.split(","))
    if args.style:
        try:
            from trarium.styles import apply as _apply_style

            _apply_style(translator, args.style)
        except Exception:
            pass

    def progress(fraction: float, message: str) -> None:
        print(f"{fraction * 100:.0f}% {message}", flush=True)

    translated = translate_cues(cues, translator, target_lang="fa",
                                max_chars=args.batch_chars, progress_callback=progress)

    (outdir / "02_farsi.fa.srt").write_text(to_srt([type(c)(c.start, c.end, t) for c, t in zip(cues, translated)]), encoding="utf-8")
    (outdir / "03_farsi.fa.vtt").write_text(to_vtt([type(c)(c.start, c.end, t) for c, t in zip(cues, translated)]), encoding="utf-8")
    (outdir / "04_bilingual.srt").write_text(to_bilingual(cues, translated), encoding="utf-8")
    (outdir / "05_transcript.txt").write_text(
        "\n".join(f"[{c.start:7.2f}] {c.text}" for c in cues), encoding="utf-8"
    )

    persian = sum(1 for text in translated for char in text if "\u0600" <= char <= "\u06ff")
    untranslated = [i for i, text in enumerate(translated) if not text.strip()]
    latin_left = sum(1 for text in translated for char in text if char.isascii() and char.isalpha())
    metrics = {
        "title": meta.get("title") or "",
        "video_id": meta.get("id") or "",
        "url": args.url or "",
        "segments_raw": len(raw_cues),
        "cues": len(cues),
        "cues_translated": len(cues) - len(untranslated),
        "untranslated_cues": untranslated,
        "duration_seconds": round(cues[-1].end if cues else 0, 1),
        "persian_chars": persian,
        "latin_chars_left": latin_left,
        "seconds": round(time.time() - started, 1),
        "providers": translator.status(),
    }
    (outdir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0 if not untranslated else 2


if __name__ == "__main__":
    raise SystemExit(main())
