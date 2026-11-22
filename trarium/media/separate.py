"""جدا کردن صدای گوینده از موزیک/پس‌زمینه با Demucs (اختیاری: pip install demucs soundfile).

برای دوبله: صدای گوینده‌ی اصلی کامل حذف می‌شود و فقط موزیک و افکت‌ها زیر دوبله می‌ماند.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from trarium.media import ffmpeg_tools as ff


def available() -> bool:
    try:
        import demucs  # noqa: F401
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def background_track(source: str | Path, rate: int, log=None, model_name: str = "htdemucs") -> np.ndarray:
    """صدای بدون گوینده (موزیک + افکت) به‌صورت mono با نرخ rate."""
    log = log or (lambda m: None)
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    model = get_model(model_name)
    model.eval()
    sr = int(model.samplerate)
    raw = ff.run_bytes(["ffmpeg", "-v", "error", "-i", str(source), "-f", "f32le", "-ac", "2", "-ar", str(sr), "-"])
    stereo = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).T.copy()
    wav = torch.from_numpy(stereo)[None]
    ref = wav.mean(1)
    mean, std = ref.mean(), ref.std() + 1e-8
    log("جدا کردن صدای گوینده از موزیک (Demucs، روی CPU کمی طول می‌کشد)…")
    with torch.no_grad():
        stems = apply_model(model, (wav - mean) / std, split=True, overlap=0.25, progress=False)[0]
    stems = stems * std + mean
    vocals = model.sources.index("vocals")
    bg = sum(stems[i] for i in range(len(model.sources)) if i != vocals).mean(0).numpy().astype(np.float32)
    if rate != sr:
        bg = ff.resample(bg, sr, rate)
    return bg
