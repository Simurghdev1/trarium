"""فراخوانی ffmpeg/ffprobe: استخراج صدا، تغییر سرعت، ساخت ویدیو و حک زیرنویس."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np

from trarium.media.timeline import atempo_chain


class MediaError(RuntimeError):
    pass


def _bin(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise MediaError(f"{name} نصب نیست؛ برای دوبله و زیرنویس‌ساز لازم است (apt install ffmpeg).")
    return path


def run(args: list[str], cwd: str | Path | None = None, timeout: int = 3600) -> str:
    proc = subprocess.run([_bin(args[0]), *args[1:]], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        raise MediaError(f"{args[0]} ناموفق بود: " + " | ".join(tail))
    return proc.stdout


def probe(path: str | Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(out or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {
        "duration": float((data.get("format") or {}).get("duration") or 0.0),
        "has_video": video is not None,
        "has_audio": audio is not None,
        "width": int(video.get("width") or 0) if video else 0,
        "height": int(video.get("height") or 0) if video else 0,
    }


def extract_audio(src: str | Path, dst: str | Path, rate: int = 16000, channels: int = 1,
                  fmt: str | None = None) -> Path:
    """صدای فایل را به wav/pcm/ogg تبدیل می‌کند (پیش‌فرض: ۱۶ کیلوهرتز مونو)."""
    dst = Path(dst)
    args = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", str(channels), "-ar", str(rate)]
    if fmt == "s16le" or dst.suffix == ".pcm":
        args += ["-f", "s16le", "-acodec", "pcm_s16le"]
    elif dst.suffix == ".ogg":
        args += ["-c:a", "libopus", "-b:a", "32k"]
    elif dst.suffix == ".mp3":
        args += ["-c:a", "libmp3lame", "-b:a", "64k"]
    args.append(str(dst))
    run(args)
    return dst


def read_mono(src: str | Path, rate: int = 24000) -> np.ndarray:
    """هر فایل صوتی → آرایهٔ float32 مونو."""
    proc = subprocess.run([_bin("ffmpeg"), "-v", "error", "-i", str(src), "-ac", "1", "-ar", str(rate),
                           "-f", "f32le", "-"], capture_output=True, timeout=600)
    if proc.returncode != 0:
        raise MediaError("خواندن صدا ناموفق بود: " + proc.stderr.decode(errors="replace")[-300:])
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def write_wav(samples: np.ndarray, dst: str | Path, rate: int = 24000, channels: int = 1) -> Path:
    dst = Path(dst)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    proc = subprocess.run([_bin("ffmpeg"), "-y", "-v", "error", "-f", "s16le", "-ar", str(rate), "-ac", str(channels),
                           "-i", "-", str(dst)], input=pcm, capture_output=True, timeout=600)
    if proc.returncode != 0:
        raise MediaError("نوشتن صدا ناموفق بود: " + proc.stderr.decode(errors="replace")[-300:])
    return dst


def pcm16_to_float(data: bytes) -> np.ndarray:
    return np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0


def change_tempo(samples: np.ndarray, tempo: float, rate: int = 24000) -> np.ndarray:
    """تغییر سرعت بدون تغییر زیر و بمی صدا (ffmpeg atempo)."""
    if abs(tempo - 1.0) < 0.01:
        return samples
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    proc = subprocess.run([_bin("ffmpeg"), "-v", "error", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "-",
                           "-filter:a", atempo_chain(tempo), "-f", "s16le", "-"],
                          input=pcm, capture_output=True, timeout=600)
    if proc.returncode != 0:
        raise MediaError("atempo ناموفق بود: " + proc.stderr.decode(errors="replace")[-300:])
    return pcm16_to_float(proc.stdout)


def mux(video_src: str | Path, audio: str | Path, dst: str | Path, subtitles_ass: str | Path | None = None,
        fonts_dir: str | Path | None = None, has_video: bool = True, duration: float | None = None) -> Path:
    """ویدیوی اصلی + صدای تازه → MP4 (H.264/AAC). اگر ورودی تصویر نداشت، پس‌زمینهٔ سیاه."""
    dst = Path(dst)
    args = ["ffmpeg", "-y", "-v", "error"]
    if has_video:
        args += ["-i", str(Path(video_src).resolve())]
    else:
        length = f"{duration or 1:.3f}"
        args += ["-f", "lavfi", "-i", f"color=c=black:s=1280x720:r=25:d={length}"]
    args += ["-i", str(Path(audio).resolve()), "-map", "0:v:0", "-map", "1:a:0"]
    cwd = None
    filters = []
    if has_video:
        # اگر صدای دوبله از ویدیو بلندتر شد (دنبالهٔ Live)، آخرین فریم نگه داشته می‌شود تا جمله بریده نشود.
        video_len = probe(video_src)["duration"]
        audio_len = probe(audio)["duration"]
        if audio_len > video_len + 0.2:
            filters.append(f"tpad=stop_mode=clone:stop_duration={audio_len - video_len:.3f}")
    if subtitles_ass:
        sub = Path(subtitles_ass)
        cwd = sub.parent
        flt = f"ass={sub.name}"
        if fonts_dir:
            flt += f":fontsdir={_filter_escape(str(Path(fonts_dir).resolve()))}"
        filters.append(flt)
    if filters:
        args += ["-vf", ",".join(filters)]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(dst.resolve())]
    run(args, cwd=cwd)
    return dst


def burn(video_src: str | Path, ass_path: str | Path, dst: str | Path, fonts_dir: str | Path | None = None) -> Path:
    """زیرنویس ASS را روی تصویر حک می‌کند (libass + HarfBuzz/FriBiDi برای فارسی)."""
    ass_path = Path(ass_path)
    flt = f"ass={ass_path.name}"
    if fonts_dir:
        flt += f":fontsdir={_filter_escape(str(Path(fonts_dir).resolve()))}"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(Path(video_src).resolve()), "-vf", flt,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(Path(dst).resolve())],
        cwd=ass_path.parent)
    return Path(dst)


def frame(video: str | Path, at: float, dst: str | Path) -> Path:
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1", str(dst)])
    return Path(dst)


def _filter_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")
