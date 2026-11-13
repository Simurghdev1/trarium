"""Fault-tolerant, token-aware translation pipeline for books."""
from __future__ import annotations

import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from threading import Lock

from rich.console import Console

from trarium.cache import TranslationCache
from trarium.checkpoint import CheckpointStore, stable_job_id
from trarium.extractors.base import ExtractedBook
from trarium.placeholders import protect, restore
from trarium.term_registry import TermRegistry
from trarium.utils import clean_translation, estimate_tokens, probably_already_in_persian, is_nontranslatable_token
from trarium.processors import TextChunker

console = Console()

SYSTEM = (
    "Translate the supplied literary text completely and faithfully into the requested target language. "
    "Output only the translation. Preserve paragraph order, dialogue structure, names, numbers and meaning. "
    "Do not summarize, omit, add commentary, or invent text."
)

@dataclass
class TranslationResult:
    title: str
    author: str
    original_lang: str
    target_lang: str
    translated_text: str = ""
    total_chunks: int = 0
    translated_chunks: int = 0
    total_chars: int = 0
    translated_chars: int = 0
    elapsed_seconds: float = 0.0
    failed_chunks: int = 0
    cache_hits: int = 0
    checkpoint_hits: int = 0
    api_requests: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    protected_spans: int = 0

class TranslationPipeline:
    def __init__(self, translator, target_lang="fa", chunk_size=14000, delay_between_requests=0.0,
                 max_workers=5, safe_optimization=True, protect_nontranslatable=True,
                 use_checkpoint=True, translation_memory=True, adaptive_batch=True,
                 registry: TermRegistry | None = None, checkpoint: CheckpointStore | None = None,
                 cache: TranslationCache | None = None):
        if max_workers < 1:
            raise ValueError("max_workers باید حداقل 1 باشد")
        self.translator = translator
        self.target_lang = target_lang
        self.chunker = TextChunker(max_chars=max(2000, int(chunk_size)), overlap=0)
        self.delay = max(0.0, float(delay_between_requests))
        self.max_workers = max_workers
        self.safe_optimization = safe_optimization
        self.protect_nontranslatable = protect_nontranslatable
        self.use_checkpoint = use_checkpoint
        self.translation_memory = translation_memory
        self.adaptive_batch = adaptive_batch
        self.registry = registry
        self.checkpoint = checkpoint or CheckpointStore()
        self.cache = cache or TranslationCache()
        self.stats = {"cache_hits":0,"checkpoint_hits":0,"api_requests":0,"in_tokens":0,"out_tokens":0,"protected_spans":0}
        self._rate_lock = Lock()
        self._next_request_at = 0.0

    def _source_fingerprint(self, book: ExtractedBook) -> str:
        h = hashlib.sha256()
        h.update((book.title or "").encode("utf-8"))
        for ch in book.chapters:
            h.update(str(ch.get("title", "")).encode("utf-8"))
            h.update(b"\0")
            h.update(str(ch.get("content", "")).encode("utf-8"))
            h.update(b"\0")
        return h.hexdigest()

    def _provider_name(self):
        return str(getattr(self.translator, "provider", "") or getattr(self.translator, "last_provider", "") or self.translator.__class__.__name__)

    def _model_name(self):
        return str(getattr(self.translator, "model", "") or getattr(self.translator, "model_name", ""))

    def _wait_rate_limit(self):
        if not self.delay:
            return
        with self._rate_lock:
            now = time.monotonic()
            wait = max(0.0, self._next_request_at - now)
            self._next_request_at = max(now, self._next_request_at) + self.delay
        if wait:
            time.sleep(wait)

    def _prepare(self, source: str):
        value = source.replace("\r\n", "\n").replace("\r", "\n")
        if self.safe_optimization:
            value = re.sub(r"[ \t]+", " ", value)
            value = re.sub(r"\n{3,}", "\n\n", value).strip()
        p = protect(value) if self.protect_nontranslatable else type("P", (), {"text":value,"mapping":{}})()
        if p.mapping:
            self.stats["protected_spans"] += len(p.mapping)
        return p.text, p.mapping

    def _prompt(self, prepared: str, registry_prompt: str = ""):
        """Build the user message only.

        The provider layer already sends its own system prompt (and repeats the
        target language), so re-sending the whole instruction block here doubled
        the input tokens of every request without changing the output.
        """
        parts = []
        if registry_prompt:
            parts.append(registry_prompt)
        if "__TRARIUM_PH_" in prepared:
            parts.append("Preserve every __TRARIUM_PH_####__ token exactly.")
        if not parts:
            return prepared
        return "\n".join(parts) + "\n\n" + prepared

    def _translated(self, source: str, key: str, prompt: str):
        provider = self._provider_name(); model = self._model_name()
        if self.translation_memory:
            hit = self.cache.get(source, self.target_lang, provider, model)
            if hit is not None:
                self.stats["cache_hits"] += 1
                return hit, True
        if self.use_checkpoint:
            # Checkpoint keys are unique to normalized source + config.
            stored = self.checkpoint.load(key)
            if "0" in stored:
                self.stats["checkpoint_hits"] += 1
                return stored["0"], True
        self._wait_rate_limit()
        self.stats["api_requests"] += 1
        out = self.translator.translate(prompt, self.target_lang)
        usage = getattr(self.translator, "last_usage", {}) or {}
        self.stats["in_tokens"] += int(usage.get("input_tokens") or estimate_tokens(prompt))
        self.stats["out_tokens"] += int(usage.get("output_tokens") or estimate_tokens(str(out)))
        out = clean_translation(str(out))
        if self.translation_memory:
            self.cache.put(source, out, self.target_lang, provider, model)
        if self.use_checkpoint:
            self.checkpoint.put(key, "0", out)
        return out, False

    def run(self, book: ExtractedBook) -> TranslationResult:
        started = time.monotonic()
        chunks = self.chunker.chunk_by_chapters(book.chapters)
        if not chunks:
            raise ValueError("متنی قابل ترجمه پیدا نشد.")
        translated_parts = [None] * len(chunks)
        job_id = stable_job_id(self._source_fingerprint(book), self.target_lang, self._provider_name(), self._model_name())

        def translate_one(idx, chunk):
            source = str(chunk.get("content", "") or "")
            if not source.strip():
                return idx, {"title":chunk.get("title","متن"),"content":"","failed":False,"error":""}
            if (self.target_lang == "fa" and probably_already_in_persian(source)) or is_nontranslatable_token(source):
                return idx, {"title":chunk.get("title","متن"),"content":source,"failed":False,"error":""}
            prepared, mapping = self._prepare(source)
            registry_prompt = self.registry.prompt(source, self.target_lang) if self.registry else ""
            prompt = self._prompt(prepared, registry_prompt)
            unit_hash = hashlib.sha256((job_id + "|" + prepared).encode("utf-8")).hexdigest()
            try:
                last = None
                for attempt in range(3):
                    try:
                        output, reused = self._translated(prepared, unit_hash, prompt)
                        output = restore(output, mapping)
                        if not output.strip():
                            raise RuntimeError("پاسخ ترجمه خالی بود")
                        return idx, {"title":chunk.get("title","متن"),"content":output,"failed":False,"error":""}
                    except Exception as exc:
                        last = exc
                        if attempt < 2:
                            time.sleep(0.8 * (2 ** attempt))
                raise RuntimeError(str(last or "ترجمه انجام نشد"))
            except Exception as exc:
                return idx, {"title":chunk.get("title","متن"),"content":source,"failed":True,"error":str(exc)}

        # Each chunk is independent. Results are placed back by index, preserving document order.
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = [pool.submit(translate_one, i, chunk) for i, chunk in enumerate(chunks)]
            for future in as_completed(futures):
                idx, result = future.result()
                translated_parts[idx] = result

        failed = [p for p in translated_parts if p and p.get("failed")]
        translated_text = "\n\n".join(f"--- {p['title']} ---\n\n{p['content']}" for p in translated_parts if p)
        return TranslationResult(
            title=book.title, author=book.author, original_lang="auto", target_lang=self.target_lang,
            translated_text=translated_text, total_chunks=len(chunks), translated_chunks=len(chunks)-len(failed),
            total_chars=book.total_chars, translated_chars=len(translated_text), elapsed_seconds=time.monotonic()-started,
            failed_chunks=len(failed), cache_hits=self.stats["cache_hits"], checkpoint_hits=self.stats["checkpoint_hits"],
            api_requests=self.stats["api_requests"], estimated_input_tokens=self.stats["in_tokens"],
            estimated_output_tokens=self.stats["out_tokens"], protected_spans=self.stats["protected_spans"])
