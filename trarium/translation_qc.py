"""Translation quality check: compare each block against its source.

This is not a second translation. It looks for the failures that actually
happened in real books:

* a sentence of the source is missing from the translation (block came back
  shorter than it should be, or the model summarised);
* the translation is padded with words that are not in the source;
* the same translated text appears for two different source blocks;
* a block came back untranslated (still mostly Latin);
* a name that the project glossary already fixed is written differently.

The result is a list of issues; the caller decides whether to report them, or to
send only those blocks back to the model for repair. Nothing here touches the
PDF.
"""
from __future__ import annotations

import re

from trarium.prompt_rules import PERSIAN_NATURAL_ORDER

_PERSIAN = re.compile(r"[\u0600-\u06ff]")
_LATIN = re.compile(r"[A-Za-z]")

# Ratio of Persian characters to source characters. Real translations of English
# into Persian land around 0.9–1.6; far outside that band something was dropped
# or invented.
MIN_RATIO = 0.35
MAX_RATIO = 3.2


def _sentences(text: str) -> int:
    parts = [p for p in re.split(r"[.!?…؟]+", text or "") if p.strip()]
    return max(1, len(parts))


def _words(text: str) -> int:
    return len([w for w in re.split(r"\s+", (text or "").strip()) if w])


def _persian_ratio(text: str) -> float:
    letters = [ch for ch in (text or "") if _PERSIAN.match(ch) or _LATIN.match(ch)]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if _PERSIAN.match(ch)) / len(letters)


def check_block(source: str, translation: str, glossary: dict[str, str] | None = None) -> list[dict]:
    """Issues for one block. An empty list means it looks fine."""
    issues: list[dict] = []
    source = (source or "").strip()
    translation = (translation or "").strip()
    if not source:
        return issues
    if not translation:
        return [{"kind": "empty", "detail": "ترجمه خالی است"}]

    persian = _persian_ratio(translation)
    if persian < 0.15:
        issues.append({"kind": "untranslated", "detail": f"فقط {persian:.0%} فارسی"})
        return issues

    ratio = len(translation) / max(1, len(source))
    if ratio < MIN_RATIO:
        issues.append({"kind": "dropped", "detail": f"طول ترجمه {ratio:.2f} برابر متن اصلی"})
    elif ratio > MAX_RATIO:
        issues.append({"kind": "expanded", "detail": f"طول ترجمه {ratio:.2f} برابر متن اصلی"})

    src_sentences = _sentences(source)
    out_sentences = _sentences(translation)
    if _words(source) > 25 and out_sentences < max(1, src_sentences - 1):
        issues.append({
            "kind": "missing_sentence",
            "detail": f"{src_sentences} جمله در اصل، {out_sentences} جمله در ترجمه",
        })

    for term, fixed in (glossary or {}).items():
        if not term or not fixed:
            continue
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", source, flags=re.I):
            if fixed not in translation:
                issues.append({"kind": "term_variant", "detail": f"«{term}» باید «{fixed}» باشد"})
    return issues


def check(pairs, glossary: dict[str, str] | None = None, *, limit: int = 200) -> dict:
    """pairs: iterable of (page, index, source, translation)."""
    issues: list[dict] = []
    seen: dict[str, tuple[int, int]] = {}
    duplicates = 0
    for page, index, source, translation in pairs:
        for issue in check_block(source, translation, glossary):
            issues.append({"page": page, "block": index, **issue})
        key = " ".join((translation or "").split())
        if len(key) > 40:
            if key in seen:
                duplicates += 1
                issues.append({
                    "page": page,
                    "block": index,
                    "kind": "duplicate",
                    "detail": f"همان متنِ صفحه {seen[key][0] + 1}",
                })
            else:
                seen[key] = (page, index)
    kinds: dict[str, int] = {}
    for issue in issues:
        kinds[issue["kind"]] = kinds.get(issue["kind"], 0) + 1
    return {
        "checked": len(pairs) if hasattr(pairs, "__len__") else None,
        "issues": issues[:limit],
        "count": len(issues),
        "by_kind": kinds,
        "duplicates": duplicates,
        "ok": not issues,
    }


REPAIR_PROMPT = (
    "The translation below is incomplete or wrong compared with the source. "
    "Translate the source again, completely, in natural Persian. Output only the "
    "translation, with every sentence and every name kept. "
    + PERSIAN_NATURAL_ORDER + "\n\n"
    "Source:\n{source}\n\nCurrent translation:\n{current}"
)


def repair_list(result: dict, pairs, max_blocks: int = 40) -> list[tuple[int, int]]:
    """Which (page, block) pairs deserve another try from the model."""
    wanted = {"dropped", "missing_sentence", "untranslated", "empty"}
    keys: list[tuple[int, int]] = []
    by_key = {(p, i): (s, t) for p, i, s, t in pairs}
    for issue in result.get("issues", []):
        if issue["kind"] not in wanted:
            continue
        key = (issue["page"], issue["block"])
        source, translation = by_key.get(key, ("", ""))
        # Only short blocks: repairing a whole page costs more than it fixes.
        if len(source) > 700:
            continue
        if key not in keys:
            keys.append(key)
        if len(keys) >= max_blocks:
            break
    return keys
