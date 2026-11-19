"""CI runner: OCR.space for scanned pages, OpenRouter (fallback Gemini) for translation.
Keys come only from env; never printed."""
from __future__ import annotations
import io, json, os, sys, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import pymupdf, requests

OCR_KEY = os.environ.get("OCR_SPACE_KEY", "").strip()
DPI = 150

def ocr_space_page(page):
    pix = page.get_pixmap(dpi=DPI, alpha=False)
    q = 85
    while True:
        data = pix.tobytes("jpeg", jpg_quality=q)
        if len(data) < 950_000 or q <= 40:
            break
        q -= 10
    for attempt in range(3):
        r = requests.post("https://api.ocr.space/parse/image",
            files={"file": ("page.jpg", data, "image/jpeg")},
            data={"apikey": OCR_KEY, "language": "eng", "isOverlayRequired": "true",
                  "OCREngine": "2", "scale": "true", "detectOrientation": "true"}, timeout=120)
        js = r.json()
        if not js.get("IsErroredOnProcessing"):
            break
        print("  OCR.space error:", js.get("ErrorMessage"), flush=True); time.sleep(5)
    s = 72.0 / DPI
    blocks = []
    for res in js.get("ParsedResults") or []:
        for line in (res.get("TextOverlay") or {}).get("Lines") or []:
            words = line.get("Words") or []
            if not words: continue
            x0 = min(w["Left"] for w in words) * s; y0 = min(w["Top"] for w in words) * s
            x1 = max(w["Left"] + w["Width"] for w in words) * s
            y1 = max(w["Top"] + w["Height"] for w in words) * s
            text = " ".join(w["WordText"] for w in words).strip()
            if len(text) < 2: continue
            blocks.append({"bbox": (x0, y0, x1, y1), "text": text, "font_size": max((y1 - y0) * 0.75, 8)})
    blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))
    return blocks

def make_translator():
    from trarium.translators.openrouter import OpenRouterTranslator
    model = os.environ.get("TRARIUM_MODEL") or "google/gemini-2.5-flash-lite"
    tr = OpenRouterTranslator(api_key=os.environ.get("OPENROUTER_API_KEY"), model_name=model)
    try:
        out = tr.translate("Hello, how are you?", "fa") if hasattr(tr, "translate") else "ok"
        if out: return tr, f"openrouter:{model}"
    except Exception as exc:
        print("  OpenRouter smoke failed:", type(exc).__name__, str(exc)[:200], flush=True)
    from trarium.translators.gemini import GeminiTranslator
    gm = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    return GeminiTranslator(api_key=os.environ.get("GEMINI_API_KEY"), model_name=gm), f"gemini:{gm}"

def main():
    from trarium.pdf_optimized import translate_pdf_optimized, extract_text_blocks
    out_dir = REPO / "ci_run" / "out"; out_dir.mkdir(parents=True, exist_ok=True)
    tr, label = make_translator()
    print("translator:", label, flush=True)
    results = []
    for src in sorted((REPO / "ci_run" / "inputs").glob("*.pdf")):
        print("==", src.name, flush=True)
        t0 = time.time()
        doc = pymupdf.open(str(src))
        pre, scanned = {}, []
        for i, p in enumerate(doc):
            if not extract_text_blocks(p):
                scanned.append(i)
                if OCR_KEY:
                    b = ocr_space_page(p); pre[i] = b
                    print(f"  OCR.space page {i+1}: {len(b)} lines", flush=True)
        doc.close()
        out = out_dir / f"{src.stem}_fa.pdf"
        res = translate_pdf_optimized(str(src), tr, "fa", str(out), enable_ocr=bool(scanned),
              precomputed_ocr=pre or None, translation_workers=2, translation_batch_chars=8000,
              return_stats=True, use_checkpoint=False, use_cache=False)
        st = res[1] if isinstance(res, tuple) else res
        info = {"file": src.name, "output": out.name, "translator": label,
                "scanned_pages": [i + 1 for i in scanned],
                "ocr_lines": {i + 1: len(v) for i, v in pre.items()},
                "seconds": round(time.time() - t0, 1)}
        for k in ("translated_blocks", "failed_blocks", "pages_written", "write_failures", "api_requests", "first_write_error"):
            info[k] = getattr(st, k, None)
        info["bytes"] = out.stat().st_size if out.exists() else 0
        if out.exists():
            d = pymupdf.open(str(out))
            for i, p in enumerate(d):
                p.get_pixmap(dpi=70).save(str(out_dir / f"{src.stem}_fa_p{i+1}.png"))
            d.close()
        print(json.dumps(info, ensure_ascii=False), flush=True)
        results.append(info)
    (out_dir / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    return 0 if all(r["bytes"] and r.get("translated_blocks") for r in results) else 1

if __name__ == "__main__":
    raise SystemExit(main())
