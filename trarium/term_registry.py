"""Persistent character / term registry for translation consistency."""
from __future__ import annotations

import json
import re
from pathlib import Path

class TermRegistry:
    def __init__(self, path: str | None = None):
        from trarium.local_state import trarium_home
        self.path = Path(path) if path else trarium_home() / "terms.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items: dict[str, str] = {}
        self.load()

    def load(self):
        try:
            self.items = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.items = {}
        return self.items

    def save(self):
        self.path.write_text(json.dumps(self.items, ensure_ascii=False, indent=2), encoding="utf-8")

    def set(self, source: str, target: str):
        source, target = str(source).strip(), str(target).strip()
        if source and target:
            self.items[source] = target
            self.save()

    def delete(self, source: str):
        if source in self.items:
            del self.items[source]
            self.save()

    def relevant(self, text: str, limit: int = 24) -> dict[str, str]:
        value = str(text or "")
        hits = [(k, v) for k, v in self.items.items() if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", value, flags=re.I)]
        return dict(hits[:limit])

    def prompt(self, text: str, target_lang: str = "fa", limit: int = 24) -> str:
        found = self.relevant(text, limit)
        if not found:
            return ""
        lines = [f"{src} -> {dst}" for src, dst in found.items()]
        return "Terminology/character consistency (follow when applicable):\n" + "\n".join(lines)
