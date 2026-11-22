"""مسیرهای کامل تب «زیرنویس» و حالت «عادی» تب «دوبله»."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from trarium.media import ffmpeg_tools as ff
from trarium.media.ass import FONTS_DIR, AssStyle, build_ass
from trarium.media.gemini_client import GeminiClient
from trarium.media.langs import is_rtl, resolve
from trarium.media.mix import mix, place_clips
from trarium.media.timeline import Segment, plan_placements, to_srt, to_vtt
from trarium.media.transcribe import transcribe
from trarium.media.translate import translate_segments
from trarium.media.tts import RATE, synthesize_all


@dataclass
class MediaResult:
    files: dict[str, Path] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)


def _logger(result: MediaResult, sink=None):
    def log(msg: str):
        result.log.append(msg)
        if sink:
            sink(msg)
    return log


def _save_segments(path: Path, segments: list[Segment]) -> None:
    path.write_text(json.dumps([s.__dict__ for s in segments], ensure_ascii=False, indent=1), encoding="utf-8")


def make_subtitles(source: Path, work: Path, *, target: str | None, translator=None,
                   client: GeminiClient | None = None, engine: str = "auto", bilingual: bool = False,
                   burn: bool = False, style: AssStyle | None = None, write_files: bool = True,
                   stem: str = "video", log=None, progress=None) -> MediaResult:
    """گفتار → زیرنویس زبان اصلی (+ ترجمه) → SRT/VTT و/یا ویدیوی با زیرنویس حک‌شده."""
    result = MediaResult()
    log = _logger(result, log)
    progress = progress or (lambda p, msg="": None)
    work.mkdir(parents=True, exist_ok=True)
    info = ff.probe(source)
    if not info["has_audio"]:
        raise ff.MediaError("فایل ورودی صدا ندارد.")
    t0 = time.time()
    progress(0.05, "استخراج گفتار")
    lang, segments, meta = transcribe(source, work, client=client, engine=engine, log=log)
    result.stats.update(duration=round(info["duration"], 2), source_language=lang, segments=len(segments),
                        transcribe=meta, transcribe_seconds=round(time.time() - t0, 1))
    log(f"{len(segments)} بخش گفتار ({lang or '?'}) با {meta['engine']}/{meta['model']}.")
    target_lang = resolve(target) if target else None
    if target_lang and target_lang.code.split("-")[0] != (lang or "").split("-")[0]:
        progress(0.45, "ترجمه")
        t1 = time.time()
        tstats = translate_segments(segments, translator, target_lang.code, log=log)
        result.stats.update(translate=tstats, translate_seconds=round(time.time() - t1, 1))
    elif target_lang:
        log("زبان مقصد با زبان گفتار یکی است؛ ترجمه لازم نیست.")
        target_lang = None
    rtl = bool(target_lang) and is_rtl(target_lang.code)
    mode = "bilingual" if (bilingual and target_lang) else ("translation" if target_lang else "original")
    suffix = target_lang.code if target_lang else (lang or "orig")
    out = work / "output"
    out.mkdir(exist_ok=True)
    _save_segments(work / "segments.json", segments)
    if write_files:
        srt = out / f"{stem}_{suffix}{'_bilingual' if mode == 'bilingual' else ''}.srt"
        srt.write_text(to_srt(segments, mode, rtl), encoding="utf-8")
        vtt = srt.with_suffix(".vtt")
        vtt.write_text(to_vtt(segments, mode, rtl), encoding="utf-8")
        result.files.update(srt=srt, vtt=vtt)
        if mode != "original":
            orig = out / f"{stem}_{lang or 'orig'}.srt"
            orig.write_text(to_srt(segments, "original"), encoding="utf-8")
            result.files["srt_original"] = orig
    if burn:
        if not info["has_video"]:
            raise ff.MediaError("برای زیرنویس حک‌شده ورودی باید تصویر داشته باشد.")
        progress(0.7, "حک زیرنویس روی تصویر")
        t2 = time.time()
        ass = out / f"{stem}_{suffix}.ass"
        ass.write_text(build_ass(segments, info["width"], info["height"], style, mode, rtl), encoding="utf-8")
        video = ff.burn(source, ass, out / f"{stem}_{suffix}_hardsub.mp4", fonts_dir=FONTS_DIR)
        result.files.update(ass=ass, hardsub=video)
        result.stats["burn_seconds"] = round(time.time() - t2, 1)
    result.segments = segments
    result.stats["target"] = target_lang.code if target_lang else None
    result.stats["total_seconds"] = round(time.time() - t0, 1)
    progress(1.0, "تمام")
    return result


def dub_normal(source: Path, work: Path, *, target: str, translator, client: GeminiClient | None = None,
               transcribe_engine: str = "auto", tts_engine: str = "auto", gender: str = "female",
               voice: str = "", audio_mode: str = "duck", bed: float = 0.18, max_tempo: float = 1.35,
               burn_subs: bool = False, style: AssStyle | None = None, stem: str = "video",
               log=None, progress=None) -> MediaResult:
    """رونویسی → ترجمه (کوتاه برای گفتار) → TTS → جا انداختن در زمان → میکس → MP4."""
    result = MediaResult()
    log = _logger(result, log)
    progress = progress or (lambda p, msg="": None)
    work.mkdir(parents=True, exist_ok=True)
    out = work / "output"
    out.mkdir(exist_ok=True)
    info = ff.probe(source)
    if not info["has_audio"]:
        raise ff.MediaError("فایل ورودی صدا ندارد.")
    total = info["duration"]
    lang = resolve(target)
    t0 = time.time()
    progress(0.05, "استخراج گفتار")
    src_lang, segments, meta = transcribe(source, work, client=client, engine=transcribe_engine, log=log)
    result.stats.update(duration=round(total, 2), source_language=src_lang, segments=len(segments),
                        transcribe=meta)
    log(f"{len(segments)} بخش گفتار ({src_lang or '?'}).")
    progress(0.3, "ترجمه")
    result.stats["translate"] = translate_segments(segments, translator, lang.code, for_speech=True, log=log)
    _save_segments(work / "segments.json", segments)
    texts = [s.translation or s.text for s in segments]
    progress(0.45, "ساخت صدا")
    clips, tts_stats = synthesize_all(texts, lang.code, work / "tts", engine=tts_engine, client=client,
                                      gender=gender, voice=voice, log=log,
                                      progress=lambda p: progress(0.45 + 0.35 * p, "ساخت صدا"))
    result.stats["tts"] = tts_stats
    placements = plan_placements(segments, [len(c) / RATE for c in clips], max_tempo=max_tempo, total=total)
    fitted, starts = [], []
    for plan, clip in zip(placements, clips):
        fitted.append(ff.change_tempo(clip, plan.tempo, RATE))
        starts.append(plan.at)
    sped = [p.tempo for p in placements if p.tempo > 1.01]
    drift = [round(p.at - s.start, 2) for p, s in zip(placements, segments)]
    result.stats["fit"] = {"sped_up": len(sped), "max_tempo": round(max(sped), 3) if sped else 1.0,
                           "max_delay_s": max(drift) if drift else 0.0}
    progress(0.85, "میکس")
    dub_track = place_clips(fitted, starts, total, RATE)
    original = ff.read_mono(source, RATE) if audio_mode != "replace" else None
    final = mix(original, dub_track, RATE, mode=audio_mode, bed=bed)
    rtl = is_rtl(lang.code)
    wav = ff.write_wav(final, work / "dub_mix.wav", RATE)
    audio = out / f"{stem}_dub_{lang.code}.m4a"
    ff.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "aac", "-b:a", "160k", str(audio)])
    voice_only = out / f"{stem}_dub_{lang.code}_voice.wav"
    ff.write_wav(dub_track, voice_only, RATE)
    srt = out / f"{stem}_{lang.code}.srt"
    # زمان زیرنویس = زمانی که صدای دوبله واقعاً پخش می‌شود.
    timed = [Segment(p.at, p.at + p.length, s.text, s.translation) for p, s in zip(placements, segments)]
    srt.write_text(to_srt(timed, "translation", rtl), encoding="utf-8")
    ass = None
    if burn_subs and info["has_video"]:
        ass = out / f"{stem}_{lang.code}.ass"
        ass.write_text(build_ass(timed, info["width"], info["height"], style, "translation", rtl), encoding="utf-8")
    progress(0.92, "ساخت MP4")
    mp4 = ff.mux(source, audio, out / f"{stem}_dub_{lang.code}.mp4", subtitles_ass=ass, fonts_dir=FONTS_DIR,
                 has_video=info["has_video"], duration=len(final) / RATE)
    result.files.update(mp4=mp4, audio=audio, voice=voice_only, srt=srt)
    result.segments = timed
    result.stats["total_seconds"] = round(time.time() - t0, 1)
    progress(1.0, "تمام")
    return result


def dub_live(source: Path, work: Path, *, target: str, client: GeminiClient, model: str | None = None,
             glossary: str = "", lag: float | None = None, audio_mode: str = "duck", bed: float = 0.18,
             translator=None, fallback_normal: bool = True, gender: str = "female",
             stem: str = "video", log=None, progress=None) -> MediaResult:
    """Gemini Live Translate: صدای مبدأ با سرعت واقعی استریم می‌شود و صدای ترجمه روی ویدیو می‌نشیند.

    زنجیرهٔ جایگزین: Live Translate (کلید ۱ → کلید ۲) → مدل native-audio زنده → اگر هیچ‌کدام نشد و
    ``fallback_normal``: دوبلهٔ عادی (رونویسی + ترجمه + Gemini TTS → edge-tts).
    """
    from trarium.media.live import LIVE_TRANSLATE_MODEL, OUT_RATE, live_dub, transcript_segments

    result = MediaResult()
    log = _logger(result, log)
    progress = progress or (lambda p, msg="": None)
    work.mkdir(parents=True, exist_ok=True)
    out = work / "output"
    out.mkdir(exist_ok=True)
    info = ff.probe(source)
    if not info["has_audio"]:
        raise ff.MediaError("فایل ورودی صدا ندارد.")
    lang = resolve(target)
    t0 = time.time()
    progress(0.05, f"استریم زنده ({info['duration']:.0f} ثانیه، با سرعت واقعی)")
    pcm = ff.extract_audio(source, work / "live_in.pcm", rate=16000).read_bytes()
    try:
        live = live_dub(pcm, lang.code, client, model=model or LIVE_TRANSLATE_MODEL, glossary=glossary,
                        lag=lag, log=log)
    except Exception as exc:
        if not (fallback_normal and translator is not None):
            raise
        log(f"دوبلهٔ زنده ممکن نشد ({str(exc)[:200]}); دوبلهٔ عادی با Gemini TTS/edge-tts اجرا می‌شود.")
        fallback = dub_normal(source, work / "normal", target=target, translator=translator, client=client,
                              gender=gender, audio_mode=audio_mode, bed=bed, stem=stem, log=log, progress=progress)
        fallback.stats["live_failed"] = str(exc)[:500]
        fallback.log[:0] = result.log
        return fallback
    result.stats.update(duration=round(info["duration"], 2), live_model=live.model, sessions=live.sessions,
                        server_messages=live.messages, audio_chunks=len(live.chunks),
                        dub_seconds=round(len(live.audio) / OUT_RATE, 2), lag_compensated=round(live.lag, 2),
                        first_audio_after=round(live.first_audio_at or 0, 2), errors=live.errors)
    progress(0.85, "میکس")
    original = ff.read_mono(source, OUT_RATE) if audio_mode != "replace" else None
    final = mix(original, live.audio, OUT_RATE, mode=audio_mode, bed=bed)
    wav = ff.write_wav(final, work / "live_mix.wav", OUT_RATE)
    audio = out / f"{stem}_live_{lang.code}.m4a"
    ff.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "aac", "-b:a", "160k", str(audio)])
    segs = transcript_segments(live.output_text, lag=live.lag)
    files = {"audio": audio}
    if segs:
        srt = out / f"{stem}_live_{lang.code}.srt"
        srt.write_text(to_srt(segs, "translation", is_rtl(lang.code)), encoding="utf-8")
        files["srt"] = srt
    src_text = " ".join(t for _, t in live.input_text).strip()
    if src_text:
        (out / f"{stem}_live_source_transcript.txt").write_text(src_text, encoding="utf-8")
    mp4 = ff.mux(source, audio, out / f"{stem}_live_{lang.code}.mp4", has_video=info["has_video"],
                 duration=len(final) / OUT_RATE)
    files["mp4"] = mp4
    result.files.update(files)
    result.segments = segs
    result.stats["total_seconds"] = round(time.time() - t0, 1)
    progress(1.0, "تمام")
    return result
