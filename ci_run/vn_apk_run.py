"""CI: translate a Ren'Py Android APK into Persian (text + font only). Keys from env only."""
from __future__ import annotations
import json, os, shutil, sys, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from trarium.translators import get_translator
from trarium.translators.fallback import FallbackTranslator
from trarium.processors.pipeline import TranslationPipeline
from trarium.vn import renpy as rp, apk as ak
from trarium.vn.build import _protected_translate

APK, OUT, SDK, UNRPYC = (Path(a) for a in sys.argv[1:5])
OUT.mkdir(parents=True, exist_ok=True)
t0 = time.time()
cands, labels = [], []
for prov, model, env in (("openrouter", "google/gemini-2.5-flash-lite", "OPENROUTER_API_KEY"),
                         ("gemini", "gemini-flash-lite-latest", "GEMINI_API_KEY"),
                         ("gemini", "gemini-2.5-flash-lite", "GEMINI_API_KEY")):
    if os.environ.get(env):
        cands.append(get_translator(prov, api_key=os.environ[env], model_name=model)); labels.append(f"{prov}:{model}")
tr = FallbackTranslator(cands, labels)
SHORT = ("این‌ها دیالوگ یک بازی ویژوال ناول هستند: فارسی محاوره‌ای، طبیعی و کوتاه بنویس، ادبی ننویس. "
         "اسم شخصیت‌ها را فارسی‌نویسی کن (Manami → مانامی). توکن‌های ⟦0⟧ را دقیقاً نگه دار. بین هر پاراگراف یک خط خالی بگذار.")
pipe = TranslationPipeline(translator=tr, target_lang="fa", short_prompt=SHORT, max_workers=2)

proj = OUT / "work" / "project"; game = proj / "game"
info = ak.unpack(APK, game)
rpys = ak.decompile(game, UNRPYC)
font_name = ak.font_of(game) or "CarroisGothic-Regular.ttf"
segs = rp.extract(game)
trs, why = _protected_translate(pipe, [s.text.replace("\n", " ") for s in segs], rp.PROTECT)
SIZES = {"say": (24, 679), "menu": (36, None), "screen_text": (20, 560), "ui": (28, None), "name": (24, None)}
shaped = []
for s, t in zip(segs, trs):
    if t is None:
        shaped.append(None); continue
    size, width = SIZES.get(s.kind, (24, None))
    shaped.append(rp.preshape(t.replace("\n", " "), ak.FONT, size, width))
review = [{"file": s.file.name, "line": s.line + 1, "kind": s.kind, "en": s.text, "fa": t} for s, t in zip(segs, trs)]
(OUT / "translation_review.json").write_text(json.dumps(review, ensure_ascii=False, indent=1), encoding="utf-8")
stats = rp.apply(segs, shaped)
(game / font_name).write_bytes(ak.FONT.read_bytes())
ak.right_align(game, 679)
shutil.make_archive(str(OUT / "translated_scripts_rpy"), "zip", game, ".", )
for p in game.glob("*.rpyc"):
    if p.name != "script_version.rpyc":
        p.unlink()
log = ak.compile_game(SDK, proj)
(OUT / "compile.log").write_text(log, encoding="utf-8")
repl = {}
for name in rpys:
    c = game / (name + "c")
    if not c.exists():
        raise SystemExit(f"compile failed for {name}: {log[-800:]}")
    repl[ak._xpath(name + "c")] = c.read_bytes()
repl[ak._xpath(font_name)] = ak.FONT.read_bytes()
out_apk = OUT / (APK.stem + "-fa.apk")
sign = ak.repack(APK, repl, out_apk, OUT / "work")
aud = ak.audit(APK, out_apk) if out_apk.exists() else {}
rep = {"engine": "renpy-android", "renpy_version": info["version"], "segments": stats["segments"], "translated": stats["written"],
       "kept_original": stats["kept_original"], "rejected": why,
       "by_kind": {k: sum(1 for s in segs if s.kind == k) for k in SIZES}, "sign": sign, "audit": aud,
       "providers": tr.status(), "seconds": round(time.time() - t0, 1)}
(OUT / "vn_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: rep[k] for k in ("renpy_version", "segments", "translated", "rejected", "sign", "seconds")}, ensure_ascii=False))
print(json.dumps(aud, ensure_ascii=False))
