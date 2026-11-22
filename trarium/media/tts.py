"""متن → گفتار برای دوبله.

engine:
  gemini  مدل TTS جمینای (اگر سهمیهٔ رایگان داشت)
  edge    edge-tts (رایگان، بدون کلید؛ fa-IR-DilaraNeural / FaridNeural و ده‌ها زبان)
  auto    اول Gemini؛ اگر سهمیه تمام شد یا خطا داد، همهٔ بخش‌ها با edge-tts ساخته می‌شوند
          تا صدای دوبله وسط ویدیو عوض نشود.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import numpy as np

from trarium.media.ffmpeg_tools import pcm16_to_float, read_mono
from trarium.media.gemini_client import GeminiClient, GeminiError, response_audio
from trarium.media.langs import edge_voice, resolve

RATE = 24000
# شناسه‌ها از models.list (2026-11): همه generateContent با خروجی AUDIO.
GEMINI_TTS_MODELS = ["gemini-2.5-flash-preview-tts", "gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts",
                     "gemini-3.1-flash-tts-preview"]
GEMINI_VOICES = {"female": "Kore", "male": "Charon"}


class TTSFailed(RuntimeError):
    pass


def gemini_tts(text: str, target: str, client: GeminiClient, gender: str = "female",
               models: list[str] | None = None) -> tuple[np.ndarray, str]:
    lang = resolve(target)
    prompt = f"Read this {lang.english} line aloud naturally, like a film dub, at a lively pace:\n{text}"
    config = {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {
        "prebuiltVoiceConfig": {"voiceName": GEMINI_VOICES.get(gender, "Kore")}}}}
    last = None
    for model in models or GEMINI_TTS_MODELS:
        try:
            data = client.generate(model, [{"parts": [{"text": prompt}]}], config=config, timeout=120)
        except GeminiError as exc:
            last = exc
            if "HTTP 404" in str(exc) or "HTTP 400" in str(exc):
                continue
            raise
        audio, mime = response_audio(data)
        if audio:
            rate = int((re.search(r"rate=(\d+)", mime or "") or [0, RATE])[1])
            samples = pcm16_to_float(audio)
            if rate != RATE:
                idx = np.linspace(0, len(samples) - 1, int(len(samples) * RATE / rate))
                samples = np.interp(idx, np.arange(len(samples)), samples).astype(np.float32)
            return samples, model
        last = GeminiError("Gemini صدا برنگرداند.")
    raise TTSFailed(f"Gemini TTS: {last}")


def edge_tts_clip(text: str, target: str, path: Path, gender: str = "female", voice: str = "") -> np.ndarray:
    try:
        import edge_tts
    except ImportError as exc:
        raise TTSFailed("edge-tts نصب نیست (pip install edge-tts).") from exc
    voice = voice or edge_voice(target, gender)
    if not voice:
        raise TTSFailed(f"صدای edge-tts برای زبان «{target}» پیدا نشد؛ نام صدا را دستی بدهید.")

    async def _run():
        await edge_tts.Communicate(text, voice).save(str(path))

    for attempt in range(3):
        try:
            asyncio.run(_run())
            break
        except Exception as exc:
            if attempt == 2:
                raise TTSFailed(f"edge-tts: {exc}") from exc
    return read_mono(path, RATE)


def trim_silence(samples: np.ndarray, threshold: float = 0.01, pad: int = int(0.04 * RATE)) -> np.ndarray:
    loud = np.flatnonzero(np.abs(samples) > threshold)
    if loud.size == 0:
        return samples
    return samples[max(0, loud[0] - pad): min(len(samples), loud[-1] + pad)]


def synthesize_all(texts: list[str], target: str, work: Path, engine: str = "auto",
                   client: GeminiClient | None = None, gender: str = "female", voice: str = "",
                   log=None, progress=None) -> tuple[list[np.ndarray], dict]:
    """همهٔ بخش‌ها با یک موتور و یک صدا. خروجی: کلیپ‌ها (float32، ۲۴ کیلوهرتز) + آمار."""
    log = log or (lambda m: None)
    work.mkdir(parents=True, exist_ok=True)
    stats = {"engine": "", "model": "", "requests": 0}
    if engine in ("auto", "gemini") and client is not None:
        clips = []
        try:
            for i, text in enumerate(texts):
                stats["requests"] += 1
                clip, model = gemini_tts(text, target, client, gender)
                clips.append(trim_silence(clip))
                stats["model"] = model
                if progress:
                    progress((i + 1) / len(texts))
            stats["engine"] = "gemini"
            return clips, stats
        except Exception as exc:
            if engine == "gemini":
                raise
            log(f"Gemini TTS در بخش {len(clips) + 1} متوقف شد ({str(exc)[:140]}); همه با edge-tts ساخته می‌شود.")
    clips = []
    for i, text in enumerate(texts):
        stats["requests"] += 1
        clips.append(trim_silence(edge_tts_clip(text, target, work / f"tts_{i:04}.mp3", gender, voice)))
        if progress:
            progress((i + 1) / len(texts))
    stats["engine"], stats["model"] = "edge-tts", voice or edge_voice(target, gender)
    return clips, stats
