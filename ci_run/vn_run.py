"""CI: translate a real Ren'Py game (The Question from the SDK) into Persian. Keys from env only."""
from __future__ import annotations
import json, os, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from trarium.translators import get_translator
from trarium.translators.fallback import FallbackTranslator
from trarium.processors.pipeline import TranslationPipeline
from trarium.vn import translate_game

src, out = Path(sys.argv[1]), Path(sys.argv[2])
cands, labels = [], []
for prov, model, env in (("openrouter", "google/gemini-2.5-flash-lite", "OPENROUTER_API_KEY"),
                         ("gemini", "gemini-flash-lite-latest", "GEMINI_API_KEY"),
                         ("gemini", "gemini-2.5-flash-lite", "GEMINI_API_KEY")):
    if os.environ.get(env):
        cands.append(get_translator(prov, api_key=os.environ[env], model_name=model)); labels.append(f"{prov}:{model}")
tr = FallbackTranslator(cands, labels)
SHORT = ("این‌ها دیالوگ یک بازی ویژوال ناول هستند: فارسی محاوره‌ای، طبیعی و کوتاه بنویس، ادبی ننویس. "
         "توکن‌های ⟦0⟧ و مانند آن را دقیقاً همان‌طور نگه دار. بین هر پاراگراف یک خط خالی بگذار.")
pipe = TranslationPipeline(translator=tr, target_lang="fa", short_prompt=SHORT, max_workers=2)
rep = translate_game(src, out, pipe)
rep["providers"] = tr.status()
(out / "vn_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: rep[k] for k in ("engine", "segments", "translated", "kept_original", "rejected", "seconds")}, ensure_ascii=False))
print(rep["game_root"])
