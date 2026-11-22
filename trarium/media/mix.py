"""ساخت ترک دوبله و میکس با صدای اصلی (numpy خالص، قابل تست)."""
from __future__ import annotations

import numpy as np


def place_clips(clips: list[np.ndarray], starts: list[float], total_seconds: float, rate: int) -> np.ndarray:
    """کلیپ‌ها را سر جایشان روی یک ترک خالی می‌گذارد (اگر از انتها بیرون زد، ترک بلندتر می‌شود)."""
    end = max([total_seconds] + [s + len(c) / rate for c, s in zip(clips, starts)])
    track = np.zeros(int(np.ceil(end * rate)) + 1, dtype=np.float32)
    for clip, start in zip(clips, starts):
        i = int(round(start * rate))
        track[i:i + len(clip)] += clip[: len(track) - i]
    return track


def duck_envelope(active: np.ndarray, rate: int, low: float = 0.18, ramp: float = 0.25) -> np.ndarray:
    """جایی که دوبله حرف می‌زند صدای اصلی تا ``low`` پایین می‌آید، با شیب نرم."""
    mask = active.astype(np.float32)
    width = max(1, int(ramp * rate))
    if width > 1:
        kernel = np.ones(width, dtype=np.float32) / width
        # گسترش ماسک کمی پیش و پس از گفتار، بعد نرم‌کردن.
        mask = np.clip(np.convolve(mask, np.ones(width, dtype=np.float32), mode="same"), 0, 1)
        mask = np.convolve(mask, kernel, mode="same")
    return (1.0 - mask * (1.0 - low)).astype(np.float32)


def activity(track: np.ndarray, threshold: float = 0.005) -> np.ndarray:
    return np.abs(track) > threshold


def normalize_peak(samples: np.ndarray, peak: float = 0.95) -> np.ndarray:
    top = float(np.max(np.abs(samples))) if samples.size else 0.0
    if top <= 1e-6:
        return samples
    return (samples * min(1.0, peak / top) if top > peak else samples).astype(np.float32)


def mix(original: np.ndarray | None, dub: np.ndarray, rate: int, mode: str = "duck",
        bed: float = 0.18, dub_gain: float = 1.0) -> np.ndarray:
    """mode: novoice (فقط موزیک بدون گوینده‌ی اصلی) | duck (اصلی زیر دوبله کم می‌شود) |
    lower (اصلی کل زمان کم) | replace (فقط دوبله)."""
    dub = dub * dub_gain
    if original is None or mode == "replace":
        return normalize_peak(dub)
    n = max(len(original), len(dub))
    a = np.zeros(n, dtype=np.float32)
    b = np.zeros(n, dtype=np.float32)
    a[: len(original)] = original
    b[: len(dub)] = dub
    if mode == "lower":
        a *= bed
    elif mode == "novoice":
        a *= duck_envelope(activity(b), rate, low=0.65)
    else:
        a *= duck_envelope(activity(b), rate, low=bed)
    return normalize_peak(a + b)


def trim_pauses(clip: np.ndarray, rate: int, max_pause: float = 0.25, keep: float = 0.12,
                threshold: float = 0.006) -> np.ndarray:
    """مکث‌های بلند داخل صدای TTS را کوتاه می‌کند تا جمله بدون تند شدن زیاد در زمانش جا شود."""
    if len(clip) == 0:
        return clip
    win = max(1, rate // 100)
    quiet = np.convolve(np.abs(clip), np.ones(win, dtype=np.float32) / win, mode="same") < threshold
    mask = np.ones(len(clip), dtype=bool)
    i, n, k = 0, len(clip), int(keep * rate)
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if j - i > int(max_pause * rate) and 0 < i and j < n:
                mask[i + k: j - k] = False
            i = j
        else:
            i += 1
    return clip[mask]
