"""Translation styles: one choice instead of a long prompt.

The user picks a style in the UI ("ادبی", "روان", "مانگایی", …) and the style
becomes a short instruction that is added to the system prompt. Keeping the
instruction short matters: a paragraph-long style prompt would be paid for on
every single block.
"""
from __future__ import annotations

STYLES: dict[str, dict] = {
    "neutral": {
        "label": "روان (پیش‌فرض)",
        "hint": "",
    },
    "literary": {
        "label": "ادبی",
        "hint": "Use literary Persian: flowing sentences, rich but not archaic vocabulary, keep the author's images.",
    },
    "plain": {
        "label": "روان و ساده",
        "hint": "Use plain, natural modern Persian. Short sentences. Understandable for a young adult reader.",
    },
    "casual": {
        "label": "خودمونی",
        "hint": "Use conversational Persian for the narration only where the source is casual; keep it readable.",
    },
    "formal": {
        "label": "رسمی",
        "hint": "Use formal written Persian suitable for documents. No colloquial words.",
    },
    "faithful": {
        "label": "وفادار به متن",
        "hint": "Stay very close to the source structure and wording; do not add or drop anything.",
    },
    "technical": {
        "label": "تخصصی",
        "hint": "Use precise technical terms; keep English terms in parentheses when no standard Persian term exists.",
    },
    "manga": {
        "label": "مانگا / کمیک",
        "hint": "Translate speech bubbles as natural spoken Persian, short lines, keeping the character's tone.",
    },
    "lightnovel": {
        "label": "لایت‌ناول",
        "hint": "Use light-novel Persian: smooth narration, natural dialogue, keep honorifics and character voice.",
    },
}


def choices() -> list[tuple[str, str]]:
    """[(id, label)] for a UI selectbox, default first."""
    return [(key, value["label"]) for key, value in STYLES.items()]


def style_instruction(style: str | None) -> str:
    """The extra system-prompt line for a style, or an empty string."""
    if not style:
        return ""
    if style in STYLES:
        return STYLES[style]["hint"]
    # A custom style: the caller passes free text, keep it short and safe.
    return f"Follow this translation style: {str(style).strip()[:300]}"


def apply(translator, style: str | None):
    """Attach the style to a translator (and to the models inside a fallback)."""
    hint = style_instruction(style)
    targets = [translator]
    for name in ("candidates", "translators", "models", "_inner", "inner"):
        inner = getattr(translator, name, None)
        if isinstance(inner, (list, tuple)):
            targets.extend(inner)
        elif inner is not None:
            targets.append(inner)
    for item in targets:
        try:
            setattr(item, "style_hint", hint)
        except Exception:
            pass
    return translator
