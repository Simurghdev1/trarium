"""Optional manga/comic image enhancement.

Originals are never modified: every function reads an image and writes a new
file. Optional AI backends (Real-ESRGAN, SwinIR) are used only when installed;
otherwise the deterministic Pillow/NumPy pipeline runs and the report says so.
"""
from __future__ import annotations

import importlib.util
import os
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

OPERATIONS = {
    "realesrgan": "Real-ESRGAN (افزایش رزولوشن با هوش مصنوعی)",
    "opencv": "OpenCV (اصلاح تصویر و کنتراست)",
    "pillow": "Pillow (مدیریت، تبدیل و بهینه‌سازی)",
    "swinir": "SwinIR (حذف نویز و بازیابی جزئیات)",
    "denoise": "Denoise (حذف نویز)",
    "deblur": "Deblur (کاهش تاری)",
    "color": "Color Restoration (اصلاح رنگ)",
    "contrast": "Contrast Enhancement (بهبود کنتراست)",
    "deskew": "Deskew (صاف‌کردن کجی)",
}
DEFAULT_OPERATIONS = tuple(k for k in OPERATIONS if k not in {"realesrgan", "swinir"})
SCALES = ("auto", "1x", "2x", "4x")
MAX_PIXELS = 80_000_000


class MangaError(RuntimeError):
    pass


@dataclass
class MangaSettings:
    scale: str = "auto"
    operations: tuple[str, ...] = DEFAULT_OPERATIONS
    auto: bool = True
    max_side: int = 4096
    jpeg_quality: int = 92

    @classmethod
    def from_dict(cls, data: dict | None) -> "MangaSettings":
        data = data or {}
        scale = str(data.get("scale") or "auto").lower()
        if scale not in SCALES:
            scale = "auto"
        ops = data.get("operations")
        if ops is None:
            ops = [name for name, enabled in (data.get("enabled") or {}).items() if enabled]
        if not ops:
            ops = list(DEFAULT_OPERATIONS)
        ops = tuple(str(op) for op in ops if str(op) in OPERATIONS)
        return cls(scale=scale, operations=ops, auto=bool(data.get("auto", True)),
                   max_side=max(512, int(data.get("max_side", 4096))),
                   jpeg_quality=max(60, min(100, int(data.get("jpeg_quality", 92)))))

    def to_dict(self) -> dict:
        return {"scale": self.scale, "operations": list(self.operations), "auto": self.auto,
                "max_side": self.max_side, "jpeg_quality": self.jpeg_quality}


def availability() -> dict[str, bool]:
    return {
        "realesrgan": bool(importlib.util.find_spec("realesrgan")),
        "swinir": bool(importlib.util.find_spec("swinir")),
        "opencv": bool(importlib.util.find_spec("cv2")),
        "pillow": True,
        "numpy": True,
    }


# --------------------------------------------------------------------- analysis
def _gray(image: Image.Image, width: int = 700) -> np.ndarray:
    value = image.convert("L")
    if value.width > width:
        value = value.resize((width, max(1, round(value.height * width / value.width))), Image.BILINEAR)
    return np.asarray(value, dtype=np.float32)


def _laplacian_variance(gray: np.ndarray) -> float:
    kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
    from numpy.lib.stride_tricks import sliding_window_view
    if min(gray.shape) < 3:
        return 0.0
    windows = sliding_window_view(gray, (3, 3))
    return float(np.var(np.tensordot(windows, kernel, axes=([2, 3], [0, 1]))))


def _noise_level(gray: np.ndarray) -> float:
    if min(gray.shape) < 3:
        return 0.0
    median = np.asarray(Image.fromarray(gray.astype(np.uint8)).filter(ImageFilter.MedianFilter(3)),
                        dtype=np.float32)
    return float(np.std(gray - median))


def _skew_angle(gray: np.ndarray) -> float:
    """Estimate skew with a projection profile on a downscaled, binarised page."""
    if min(gray.shape) < 20:
        return 0.0
    ink = (gray < max(80.0, float(np.percentile(gray, 35)))).astype(np.float32)
    if ink.mean() < 0.002 or ink.mean() > 0.6:
        return 0.0
    base = Image.fromarray((ink * 255).astype(np.uint8))
    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-7.0, 7.01, 1.0):
        rotated = base.rotate(float(angle), resample=Image.BILINEAR, expand=True, fillcolor=0)
        profile = np.asarray(rotated, dtype=np.float32).sum(axis=1)
        score = float(np.var(np.diff(profile)))
        if score > best_score:
            best_angle, best_score = float(angle), score
    return best_angle


def analyze(image: Image.Image) -> dict:
    gray = _gray(image)
    p5, p95 = (float(np.percentile(gray, q)) for q in (5, 95))
    rgb = np.asarray(image.convert("RGB").resize((64, 64)), dtype=np.float32)
    means = rgb.reshape(-1, 3).mean(axis=0)
    cast = float(np.max(means) - np.min(means))
    return {
        "width": image.width, "height": image.height, "mode": image.mode,
        "blur": round(_laplacian_variance(gray), 2),
        "noise": round(_noise_level(gray), 2),
        "skew": round(_skew_angle(gray), 2),
        "contrast": round(p95 - p5, 1),
        "brightness": round(float(gray.mean()), 1),
        "color_cast": round(cast, 1),
        "megapixels": round(image.width * image.height / 1_000_000, 2),
    }


def plan(image: Image.Image, settings: MangaSettings) -> tuple[dict, dict, list[str]]:
    """Return (analysis, sizes, ordered steps) without touching the image."""
    info = analyze(image)
    steps: list[str] = []
    enabled = set(settings.operations)
    if settings.scale in {"2x", "4x"}:
        factor = int(settings.scale[0])
    elif settings.scale == "1x":
        factor = 1
    else:
        # Auto: small scans gain from 2x; already-large pages do not.
        factor = 2 if max(image.width, image.height) < 1600 else 1
    if "realesrgan" in enabled and factor > 1:
        steps.append("realesrgan")
    elif "swinir" in enabled and factor > 1:
        steps.append("swinir")
    if "deskew" in enabled and abs(info["skew"]) >= 0.8:
        steps.append("deskew")
    if "denoise" in enabled and info["noise"] >= 6.0:
        steps.append("denoise")
    if "deblur" in enabled and info["blur"] < 90.0:
        steps.append("deblur")
    if "color" in enabled and info["color_cast"] >= 14.0:
        steps.append("color")
    if "contrast" in enabled and info["contrast"] <= 190.0:
        steps.append("contrast")
    if "opencv" in enabled and info["contrast"] <= 190.0 and "contrast" not in steps:
        steps.append("opencv")
    if factor > 1:
        steps.append("scale")
    # Pillow is the writer, always used; keep it in the report.
    quality_ok = (info["width"] >= 1600 and info["noise"] < 6.0 and info["blur"] >= 90.0
                  and abs(info["skew"]) < 0.8 and info["contrast"] > 190.0)
    if quality_ok and settings.scale == "auto":
        steps = []
    return info, {"factor": factor, "quality_ok": quality_ok}, steps


# --------------------------------------------------------------------- operations
def _best_scale(image: Image.Image, settings: MangaSettings, factor: int, report: dict) -> Image.Image:
    enabled = set(settings.operations)
    ai_name = "realesrgan" if "realesrgan" in enabled else ("swinir" if "swinir" in enabled else "")
    if ai_name == "realesrgan":
        try:
            image = _realesrgan_upscale(image, factor)
            report["steps"].append({"name": "realesrgan", "applied": True,
                                    "detail": f"{factor}x با Real-ESRGAN"})
            return image
        except Exception as exc:
            report["steps"].append({"name": "realesrgan", "applied": False,
                                    "detail": f"در دسترس نبود ({exc})؛ از Lanczos استفاده شد"})
    if ai_name == "swinir":
        try:
            image = _swinir_upscale(image, factor)
            report["steps"].append({"name": "swinir", "applied": True,
                                    "detail": f"{factor}x با SwinIR"})
            return image
        except Exception as exc:
            report["steps"].append({"name": "swinir", "applied": False,
                                    "detail": f"در دسترس نبود ({exc})؛ از Lanczos استفاده شد"})
    target = (max(1, round(image.width * factor)), max(1, round(image.height * factor)))
    if target[0] * target[1] > MAX_PIXELS:
        raise MangaError("رزولوشن خروجی بیش از حد بزرگ می‌شود؛ سقف ابعاد را کم کنید.")
    resized = image.resize(target, Image.LANCZOS)
    report["steps"].append({"name": "scale", "applied": True,
                            "detail": f"{factor}x با Lanczos ({target[0]}×{target[1]})"})
    return resized


def _realesrgan_upscale(image: Image.Image, factor: int) -> Image.Image:
    weights = os.environ.get("TRARIUM_REALESRGAN_MODEL", "").strip()
    if not weights or not Path(weights).exists():
        raise MangaError("وزن‌های Real-ESRGAN تنظیم نشده است (TRARIUM_REALESRGAN_MODEL)")
    from basicsr.archs.rrdbnet_arch import RRDBNet  # type: ignore
    from realesrgan import RealESRGANer  # type: ignore
    model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
    upsampler = RealESRGANer(scale=4, model_path=weights, model=model, tile=256, half=False)
    output, _ = upsampler.enhance(np.asarray(image.convert("RGB"))[:, :, ::-1], outscale=factor)
    return Image.fromarray(output[:, :, ::-1])


def _swinir_upscale(image: Image.Image, factor: int) -> Image.Image:
    raise MangaError("SwinIR به وزن‌های مدل و نصب دستی نیاز دارد")


def _deskew(image: Image.Image, angle: float) -> Image.Image:
    return image.rotate(float(angle), resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))


def _denoise(image: Image.Image, strength: float = 0.65) -> Image.Image:
    filtered = image.filter(ImageFilter.MedianFilter(3))
    return Image.blend(image, filtered, strength)


def _deblur(image: Image.Image) -> Image.Image:
    return image.filter(ImageFilter.UnsharpMask(radius=1.7, percent=140, threshold=4))


def _restore_color(image: Image.Image) -> Image.Image:
    array = np.asarray(image.convert("RGB"), dtype=np.float32)
    means = array.reshape(-1, 3).mean(axis=0)
    if float(means.max()) <= 0:
        return image
    target = float(means.mean())
    gains = np.clip(target / np.maximum(means, 1.0), 0.92, 1.08)
    balanced = np.clip(array * gains, 0, 255).astype(np.uint8)
    return Image.fromarray(balanced)


def _contrast(image: Image.Image) -> Image.Image:
    if importlib.util.find_spec("cv2"):
        try:
            import cv2  # type: ignore
            array = np.asarray(image.convert("RGB"))
            lab = cv2.cvtColor(array, cv2.COLOR_RGB2LAB)
            l, a, b = cv2.split(lab)
            l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
            return Image.fromarray(cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2RGB))
        except Exception:
            pass
    return ImageOps.autocontrast(image, cutoff=1)


def enhance(image: Image.Image, settings: MangaSettings) -> tuple[Image.Image, dict]:
    started = time.monotonic()
    source_mode = image.mode
    work = image.copy()
    info, size_info, steps = plan(image, settings)
    report: dict = {"ok": True, "steps": [], "analysis": info, "scale": settings.scale,
                    "original": [image.width, image.height], "error": ""}
    if work.mode not in {"RGB", "RGBA", "L"}:
        work = work.convert("RGBA" if "A" in work.getbands() else "RGB")
    try:
        if "deskew" in steps and abs(info["skew"]) >= 0.8:
            work = _deskew(work, info["skew"])
            report["steps"].append({"name": "deskew", "applied": True,
                                    "detail": f"{info['skew']:.1f} درجه"})
        if "denoise" in steps:
            work = _denoise(work)
            report["steps"].append({"name": "denoise", "applied": True,
                                    "detail": f"نویز {info['noise']:.1f}"})
        if "deblur" in steps:
            work = _deblur(work)
            report["steps"].append({"name": "deblur", "applied": True,
                                    "detail": f"وضوح {info['blur']:.1f}"})
        if "color" in steps:
            work = _restore_color(work)
            report["steps"].append({"name": "color", "applied": True,
                                    "detail": f"انحراف رنگ {info['color_cast']:.1f}"})
        if "contrast" in steps or "opencv" in steps:
            work = _contrast(work)
            report["steps"].append({"name": "contrast", "applied": True,
                                    "detail": f"کنتراست {info['contrast']:.1f}"})
        factor = int(size_info["factor"])
        if factor > 1:
            work = _best_scale(work, settings, factor, report)
        elif settings.scale in {"2x", "4x"}:
            work = _best_scale(work, settings, int(settings.scale[0]), report)
        if max(work.width, work.height) > settings.max_side:
            ratio = settings.max_side / max(work.width, work.height)
            work = work.resize((max(1, round(work.width * ratio)), max(1, round(work.height * ratio))),
                               Image.LANCZOS)
            report["steps"].append({"name": "limit", "applied": True,
                                    "detail": f"محدود به {settings.max_side} پیکسل"})
        if not report["steps"]:
            report["steps"].append({"name": "pillow", "applied": True,
                                    "detail": "کیفیت کافی بود؛ فقط بهینه‌سازی/ذخیره انجام شد"})
    except Exception as exc:
        report["ok"] = False
        report["error"] = str(exc)
        # A failed enhancement must never lose the page.
        return image.copy(), report
    if "A" not in work.getbands() and "A" in source_mode:
        work = work.convert("RGBA")
    report["enhanced"] = [work.width, work.height]
    report["duration_ms"] = round((time.monotonic() - started) * 1000)
    return work, report


def enhance_file(source: str | Path, destination: str | Path, settings: MangaSettings) -> dict:
    """Enhance one file into a new path. The source file is only read."""
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as opened:
            enhanced, report = enhance(opened, settings)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "source": str(source), "steps": []}
    suffix = destination.suffix.lower() or ".png"
    if suffix in {".jpg", ".jpeg"}:
        enhanced.convert("RGB").save(destination, "JPEG", quality=settings.jpeg_quality, optimize=True)
    elif suffix == ".webp":
        enhanced.save(destination, "WEBP", quality=settings.jpeg_quality, method=4)
    elif suffix == ".bmp":
        enhanced.convert("RGB").save(destination, "BMP")
    elif suffix in {".tif", ".tiff"}:
        enhanced.save(destination, "TIFF", compression="tiff_deflate")
    else:
        enhanced.save(destination, "PNG", optimize=True)
    report.update({"source": str(source), "output": str(destination),
                   "output_bytes": destination.stat().st_size if destination.exists() else 0})
    return report


def enhance_archive(source: str | Path, destination: str | Path, settings: MangaSettings,
                    progress=None) -> dict:
    """Enhance every image inside a ZIP/CBZ into a new ZIP; originals stay intact."""
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    reports: list[dict] = []
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as out:
        images = [n for n in archive.namelist() if n.lower().endswith(
            (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"))]
        for i, name in enumerate(images, 1):
            data = archive.read(name)
            temp = destination.parent / f"__tmp_{i}{Path(name).suffix.lower()}"
            temp.write_bytes(data)
            try:
                report = enhance_file(temp, destination.parent / Path(name).name, settings)
                reports.append(report)
                if report.get("output") and Path(report["output"]).exists():
                    out.write(report["output"], Path(name).name)
                else:
                    out.writestr(Path(name).name, data)
            finally:
                temp.unlink(missing_ok=True)
            if progress:
                progress(i, len(images), name)
    return {"ok": bool(reports), "count": len(reports), "reports": reports}
