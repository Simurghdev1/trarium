"""پایپلاین اصلی ترجمه کتاب."""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from dataclasses import dataclass
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

from trarium.extractors.base import ExtractedBook
from trarium.translators.base import BaseTranslator
from trarium.processors import TextChunker
from trarium.utils import probably_already_in_persian

console = Console()

@dataclass
class TranslationResult:
    """نتیجه ترجمه کامل."""
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


class TranslationPipeline:
    """پایپلاین اصلی ترجمه کتاب."""

    def __init__(
        self,
        translator: BaseTranslator,
        target_lang: str = "fa",
        chunk_size: int = 15000,
        delay_between_requests: float = 0.0,
        max_workers: int = 5,
    ):
        self.translator = translator
        self.target_lang = target_lang
        # Overlap is useful as model context only when the merge stage can
        # remove duplicated output. This pipeline does not do that, so disable
        # overlap here to prevent translated text from being duplicated.
        self.chunker = TextChunker(max_chars=chunk_size, overlap=0)
        self.delay = delay_between_requests
        if max_workers < 1:
            raise ValueError("max_workers باید حداقل 1 باشد")
        if delay_between_requests < 0:
            raise ValueError("delay_between_requests نمی‌تواند منفی باشد")
        self.max_workers = max_workers

    def run(self, book: ExtractedBook) -> TranslationResult:
        """ترجمه کامل کتاب رو اجرا می‌کنه."""
        start_time = time.time()

        console.print("\n[bold cyan]📦 تقسیم‌بندی متن...[/]")
        chunks = self.chunker.chunk_by_chapters(book.chapters)
        total_chunks = len(chunks)
        if total_chunks == 0:
            raise ValueError("متنی قابل ترجمه پیدا نشد (فصل یا محتوای معتبر وجود ندارد).")

        console.print(f"  📊 تعداد بخش‌ها: {total_chunks}")
        console.print(f"  📏 اندازه هر بخش: حداکثر ~{self.chunker.max_chars:,} کاراکتر")
        console.print(f"  ⚡ ترجمه موازی: {self.max_workers} درخواست همزمان")

        translated_parts = [None] * total_chunks
        completed_count = [0]
        rate_lock = Lock()
        next_request_at = [0.0]

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("ترجمه...", total=total_chunks)

            def _translate_one(idx, chunk):
                # Enforce delay between request starts without serializing the
                # requests themselves when workers > 1.
                if self.delay > 0:
                    with rate_lock:
                        now = time.monotonic()
                        wait = max(0.0, next_request_at[0] - now)
                        next_request_at[0] = max(now, next_request_at[0]) + self.delay
                    if wait:
                        time.sleep(wait)
                t0 = time.time()
                try:
                    source_text = chunk["content"]
                    if self.target_lang == "fa" and probably_already_in_persian(source_text):
                        translated = source_text
                    else:
                        translated = self.translator.translate(source_text, self.target_lang)
                    elapsed = time.time() - t0
                    return idx, {
                        "title": chunk["title"],
                        "content": translated,
                        "failed": False,
                    }, elapsed
                except Exception as e:
                    elapsed = time.time() - t0
                    return idx, {
                        "title": chunk["title"],
                        "content": "",
                        "failed": True,
                        "error": str(e),
                    }, elapsed

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(_translate_one, i, chunk): i
                    for i, chunk in enumerate(chunks)
                }

                for future in as_completed(futures):
                    idx, part, elapsed = future.result()
                    translated_parts[idx] = part
                    completed_count[0] += 1
                    progress.update(
                        task,
                        advance=1,
                        description=f"ترجمه بخش {completed_count[0]}/{total_chunks}",
                    )

        successful_parts = [p for p in translated_parts if p is not None and not p.get("failed")]
        failed_parts = [p for p in translated_parts if p is not None and p.get("failed")]
        translated_text = "\n\n".join(
            f"--- {part['title']} ---\n\n{part['content']}"
            for part in successful_parts
        )

        elapsed = time.time() - start_time

        result = TranslationResult(
            title=book.title,
            author=book.author,
            original_lang="auto",
            target_lang=self.target_lang,
            translated_text=translated_text,
            total_chunks=total_chunks,
            translated_chunks=len(successful_parts),
            total_chars=book.total_chars,
            translated_chars=len(translated_text),
            elapsed_seconds=elapsed,
        )

        if failed_parts:
            console.print(f"[yellow]⚠️ {len(failed_parts)} بخش ترجمه نشد.[/]")
        self._print_summary(result)
        return result

    def _print_summary(self, result: TranslationResult):
        status = "✅ ترجمه کامل شد!" if result.translated_chunks == result.total_chunks else "⚠️ ترجمه با خطا تمام شد"
        console.print(f"\n[bold green]{status}[/]")
        console.print(f"  📖 کتاب: {result.title}")
        console.print(f"  ✍️  نویسنده: {result.author}")
        console.print(f"  📊 بخش‌ها: {result.translated_chunks}/{result.total_chunks}")
        console.print(f"  📏 کاراکتر اصلی: {result.total_chars:,}")
        console.print(f"  📏 کاراکتر ترجمه: {result.translated_chars:,}")
        console.print(f"  ⏱️  زمان: {result.elapsed_seconds:.1f} ثانیه")
        console.print(f"  💰 هزینه: $0 (رایگان)\n")
