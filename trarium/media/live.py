"""دوبلهٔ زنده با Gemini Live API.

مدل اصلی: ``gemini-3.5-live-translate-preview`` (Live Translate: گفتار ← ترجمه ← گفتار
همزمان، ۷۰+ زبان، در AI Studio رایگان). اگر در دسترس نبود، مدل native-audio با
دستور سیستمی «مترجم همزمان» جایگزین می‌شود.

صدای مبدأ به صورت PCM شانزده‌بیتی ۱۶ کیلوهرتز در تکه‌های ۱۰۰ میلی‌ثانیه‌ای با
سرعت واقعی فرستاده می‌شود؛ صدای خروجی (۲۴ کیلوهرتز) با زمان رسیدنش روی
تایم‌لاین ویدیو گذاشته می‌شود و تأخیر مدل (چند ثانیه) با جابه‌جایی جبران می‌شود.

احراز هویت: با کلید یک توکن یک‌بارمصرف (ephemeral) گرفته می‌شود و WebSocket
فقط با همان توکن باز می‌شود؛ کلید اصلی وارد URL نمی‌شود.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from trarium.media.gemini_client import GeminiClient, GeminiError
from trarium.media.langs import resolve

LIVE_TRANSLATE_MODEL = "gemini-3.5-live-translate-preview"
NATIVE_AUDIO_FALLBACK = "gemini-2.5-flash-native-audio-latest"
WS_BASE = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService."
IN_RATE, OUT_RATE = 16000, 24000
CHUNK = IN_RATE // 10 * 2  # 100ms of s16le mono


@dataclass
class LiveResult:
    model: str = ""
    audio: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    chunks: list[tuple[float, int]] = field(default_factory=list)   # (ثانیهٔ رسیدن، تعداد نمونه)
    input_text: list[tuple[float, str]] = field(default_factory=list)
    output_text: list[tuple[float, str]] = field(default_factory=list)
    messages: int = 0
    sessions: int = 0
    first_audio_at: float | None = None
    lag: float = 0.0
    failures: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def is_quota_error(text: str) -> bool:
    lowered = (text or "").lower()
    return any(k in lowered for k in ("429", "resource_exhausted", "resource exhausted", "quota", "rate limit"))


def setup_message(model: str, target_code: str, english: str, glossary: str = "") -> dict:
    if model == LIVE_TRANSLATE_MODEL or "translate" in model:
        setup = {"model": f"models/{model}", "inputAudioTranscription": {}, "outputAudioTranscription": {},
                 "generationConfig": {"responseModalities": ["AUDIO"],
                                      "translationConfig": {"targetLanguageCode": target_code,
                                                            "echoTargetLanguage": False}}}
        if glossary.strip():
            setup["systemInstruction"] = {"parts": [{"text": glossary.strip()}]}
        return {"setup": setup}
    instruction = (f"You are a professional simultaneous interpreter dubbing a video. Translate everything you hear "
                   f"into {english} and speak only the translation, in a natural voice, matching the speaker's tone. "
                   "Never answer, comment, greet or add anything. If there is no speech, stay silent. " + glossary)
    return {"setup": {"model": f"models/{model}", "inputAudioTranscription": {}, "outputAudioTranscription": {},
                      "systemInstruction": {"parts": [{"text": instruction}]},
                      "generationConfig": {"responseModalities": ["AUDIO"]}}}


def place_live_chunks(chunks: list[tuple[float, np.ndarray]], lag: float, rate: int = OUT_RATE,
                      gap_reset: float = 0.6) -> list[tuple[float, np.ndarray]]:
    """زمان رسیدن هر تکه → جای آن روی تایم‌لاین (خالص، قابل تست).

    تکه‌های پشت‌سرهم یک جمله پیوسته می‌مانند؛ اگر بین دو تکه بیش از ``gap_reset``
    ثانیه فاصله افتاد، جملهٔ تازه از «زمان رسیدن − تأخیر» شروع می‌شود.
    """
    placed, cursor = [], 0.0
    for arrival, samples in chunks:
        target = max(0.0, arrival - lag)
        at = cursor if target <= cursor + gap_reset else target
        at = max(at, cursor)
        placed.append((at, samples))
        cursor = at + len(samples) / rate
    return placed


def speech_onset(pcm16: bytes, rate: int = IN_RATE, threshold: float = 0.02) -> float:
    samples = np.frombuffer(pcm16[: len(pcm16) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    win = rate // 20
    for i in range(0, max(0, len(samples) - win), win):
        if np.sqrt(np.mean(samples[i:i + win] ** 2)) > threshold:
            return i / rate
    return 0.0


async def _session(client: GeminiClient, model: str, pcm: bytes, offset: float, target: str, english: str,
                   glossary: str, speed: float, tail_wait: float, result: LiveResult,
                   raw_chunks: list, log) -> None:
    import aiohttp

    token = client.ephemeral_token()
    url = WS_BASE + "BidiGenerateContentConstrained?access_token=" + token
    async with aiohttp.ClientSession(trust_env=True) as http:
        async with http.ws_connect(url, max_msg_size=0, heartbeat=20) as ws:
            await ws.send_str(json.dumps(setup_message(model, target, english, glossary)))
            first = await ws.receive(timeout=30)
            if first.type != aiohttp.WSMsgType.TEXT and first.type != aiohttp.WSMsgType.BINARY:
                raise GeminiError(f"Live setup رد شد: {first.data} {first.extra or ''}")
            if "setupComplete" not in (first.data if isinstance(first.data, str) else first.data.decode()):
                raise GeminiError(f"Live setup پاسخ غیرمنتظره داد: {str(first.data)[:200]}")
            result.sessions += 1
            t0 = time.monotonic()
            done_sending = asyncio.Event()
            last_msg = [time.monotonic()]
            last_audio = [time.monotonic()]

            async def sender():
                step = 0.1 / max(speed, 0.1)
                for i in range(0, len(pcm), CHUNK):
                    piece = pcm[i:i + CHUNK]
                    await ws.send_str(json.dumps({"realtimeInput": {"audio": {
                        "data": base64.b64encode(piece).decode(), "mimeType": f"audio/pcm;rate={IN_RATE}"}}}))
                    delay = t0 + (i // CHUNK + 1) * step - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                # چند ثانیه سکوت تا جملهٔ آخر هم ترجمه شود.
                silence = bytes(CHUNK)
                for _ in range(20):
                    await ws.send_str(json.dumps({"realtimeInput": {"audio": {
                        "data": base64.b64encode(silence).decode(), "mimeType": f"audio/pcm;rate={IN_RATE}"}}}))
                    await asyncio.sleep(step)
                await ws.send_str(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                done_sending.set()

            sent_at = [0.0]

            async def receiver():
                while True:
                    if done_sending.is_set():
                        if not sent_at[0]:
                            sent_at[0] = time.monotonic()
                        quiet = time.monotonic() - last_audio[0] > tail_wait
                        # سقف سخت: بعد از پایان ارسال حداکثر ۳ برابر tail_wait منتظر می‌مانیم.
                        if quiet or time.monotonic() - sent_at[0] > tail_wait * 3:
                            return
                    try:
                        msg = await ws.receive(timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        if not done_sending.is_set():
                            result.errors.append(f"اتصال Live بسته شد: {msg.data} {msg.extra or ''}")
                        return
                    data = json.loads(msg.data if isinstance(msg.data, str) else msg.data.decode())
                    now = time.monotonic() - t0 + offset
                    last_msg[0] = time.monotonic()
                    result.messages += 1
                    content = data.get("serverContent") or {}
                    for part in (content.get("modelTurn") or {}).get("parts") or []:
                        inline = part.get("inlineData") or {}
                        if inline.get("data"):
                            audio = base64.b64decode(inline["data"])
                            samples = np.frombuffer(audio[: len(audio) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
                            if np.abs(samples).max(initial=0) > 0.002:
                                last_audio[0] = time.monotonic()
                            raw_chunks.append((now, samples))
                            if result.first_audio_at is None:
                                result.first_audio_at = now
                    if (content.get("inputTranscription") or {}).get("text"):
                        result.input_text.append((now, content["inputTranscription"]["text"]))
                    if (content.get("outputTranscription") or {}).get("text"):
                        result.output_text.append((now, content["outputTranscription"]["text"]))
                    if result.messages % 100 == 0:
                        log(f"Live: {result.messages} پیام، {len(raw_chunks)} تکهٔ صدا.")
                    if data.get("goAway"):
                        log("سرور Live پیام goAway داد.")

            await asyncio.gather(sender(), receiver())


def live_dub(pcm16k: bytes, target: str, client: GeminiClient, *, model: str = LIVE_TRANSLATE_MODEL,
             fallback: str = NATIVE_AUDIO_FALLBACK, glossary: str = "", lag: float | None = None,
             speed: float = 1.0, window_s: float = 480.0, tail_wait: float = 6.0, log=None) -> LiveResult:
    """کل صدا را (در پنجره‌های ≤۸ دقیقه، هر کدام یک نشست) به Live می‌دهد و صدای دوبله را برمی‌گرداند."""
    log = log or (lambda m: None)
    lang = resolve(target)
    result = LiveResult()
    raw: list[tuple[float, np.ndarray]] = []
    window = int(window_s * IN_RATE) * 2
    models = [model] + ([fallback] if fallback and fallback != model else [])
    for candidate in models:
        while True:
            result.errors.clear()
            raw.clear()
            result.model = candidate
            try:
                for start in range(0, len(pcm16k), window):
                    offset = start / 2 / IN_RATE
                    asyncio.run(_session(client, candidate, pcm16k[start:start + window], offset, lang.code,
                                         lang.english, glossary, speed, tail_wait, result, raw, log))
                if not raw:
                    result.errors.append(f"{candidate}: هیچ صدایی برنگشت.")
            except Exception as exc:
                result.errors.append(f"{candidate}: {str(exc)[:300]}")
                log(f"Live با {candidate} ناموفق بود: {str(exc)[:200]}")
            if raw:
                break
            # سهمیه/محدودیت: اول کلید بعدی با همین مدل، بعد مدل بعدی.
            if is_quota_error(" ".join(result.errors)) and client.index + 1 < len(client.keys):
                client.index += 1
                log(f"سهمیهٔ Live روی کلید {client.index} تمام شد؛ کلید {client.index + 1}.")
                continue
            break
        if raw:
            break
        result.failures.extend(result.errors)
    if not raw:
        raise GeminiError("دوبلهٔ زنده صدایی نساخت: " + " | ".join(result.failures or result.errors))
    if lag is None:
        onset = speech_onset(pcm16k)
        lag = min(5.0, max(0.0, (result.first_audio_at or onset) - onset))
    result.chunks = [(t, len(s)) for t, s in raw]
    placed = place_live_chunks(raw, lag)
    end = max(at + len(s) / OUT_RATE for at, s in placed)
    track = np.zeros(int(np.ceil(end * OUT_RATE)) + 1, dtype=np.float32)
    for at, samples in placed:
        i = int(round(at * OUT_RATE))
        track[i:i + len(samples)] += samples
    result.audio = track
    log(f"Live: {len(raw)} تکهٔ صدا، {len(track) / OUT_RATE:.1f} ثانیه، تأخیر جبران‌شده {lag:.2f} ثانیه.")
    result.lag = lag
    return result


def transcript_segments(items: list[tuple[float, str]], gap: float = 1.2, lag: float = 0.0):
    """متن خروجی Live (تکه‌تکه با زمان رسیدن) → بخش‌های تقریبی برای SRT."""
    from trarium.media.timeline import Segment
    segs: list[Segment] = []
    for t, text in items:
        t = max(0.0, t - lag)
        if segs and t - segs[-1].end <= gap and not segs[-1].text.rstrip().endswith((".", "!", "?", "؟")):
            segs[-1].text += text
            segs[-1].end = t + 0.5
        else:
            segs.append(Segment(t, t + 0.8, text))
    for s in segs:
        s.text = " ".join(s.text.split())
        s.translation = s.text
    return [s for s in segs if s.text]
